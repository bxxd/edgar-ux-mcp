"""
EDGAR Adapter

Implements FilingFetcher port using edgartools library.
"""
import time
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FutureTimeoutError

from edgar import Company, set_identity, get_current_filings, get_ticker_to_cik_lookup
from edgar.current_filings import get_current_entries_on_page

# Import httpx exceptions for better error handling
try:
    from httpx import ReadTimeout, ConnectTimeout, TimeoutException
except ImportError:
    ReadTimeout = Exception
    ConnectTimeout = Exception
    TimeoutException = Exception

from ..core.domain import Filing
from ..core.ports import FilingFetcher


# Time-based cache for current filings to avoid hammering SEC.gov
# Cache TTL: 90 seconds (balances freshness vs. load)
class TTLCache:
    """Simple time-to-live cache with stale-while-revalidate support"""
    def __init__(self, ttl_seconds: int = 90, stale_ttl_seconds: int = 300):
        self.ttl = ttl_seconds
        self.stale_ttl = stale_ttl_seconds  # How long to keep stale data
        self.cache = {}
        self.timestamps = {}

    def get(self, key, allow_stale: bool = False):
        """Get cached value. If allow_stale=True, return stale data if fresh is unavailable."""
        if key in self.cache:
            age = time.time() - self.timestamps[key]
            if age < self.ttl:
                # Fresh data
                return self.cache[key], True
            elif allow_stale and age < self.stale_ttl:
                # Stale but acceptable
                return self.cache[key], False
            else:
                # Too old, remove
                del self.cache[key]
                del self.timestamps[key]
        return None, False

    def set(self, key, value):
        self.cache[key] = value
        self.timestamps[key] = time.time()


# Global TTL cache for current filings
# Fresh: 90s, Stale-acceptable: 24 hours (serve during SEC.gov outages)
_current_filings_cache = TTLCache(ttl_seconds=90, stale_ttl_seconds=86400)

# Core form types for 'CORE' filter - essential filings only
CORE_FORM_TYPES = {
    # Annual/Quarterly reports (US companies)
    '10-K', '10-K/A', '10-Q', '10-Q/A',
    # Annual/Quarterly reports (Foreign companies)
    '20-F', '20-F/A', '6-K', '6-K/A',
    # Current reports (material events)
    '8-K', '8-K/A',
    # Registration statements (IPOs, secondaries, M&A)
    'S-1', 'S-1/A', 'S-3', 'S-3/A', 'S-4', 'S-4/A',
    # Note: 13D/13G excluded - too noisy (passive holder filings)
    # Use ALL with ticker or specific form type to find ownership filings
}


class EdgarAdapter(FilingFetcher):
    """EDGAR filing fetcher using edgartools"""

    def __init__(self, user_agent: str = "breed research breed@idio.sh"):
        set_identity(user_agent)
        # Lazy-load CIK-to-ticker mapping (only when needed)
        self._cik_to_ticker = None

    def _get_cik_to_ticker_mapping(self) -> dict[int, str]:
        """Get CIK-to-ticker mapping (lazy-loaded and cached)"""
        if self._cik_to_ticker is None:
            ticker_to_cik = get_ticker_to_cik_lookup()
            self._cik_to_ticker = {int(cik): ticker.upper() for ticker, cik in ticker_to_cik.items()}
        return self._cik_to_ticker

    def list_available(self, ticker: Optional[str], form_type: str) -> list[Filing]:
        """
        List all available filings from SEC (historical + current).

        If ticker is None, returns latest filings across all companies.
        If form_type is 'ALL' or empty string, returns all form types.
        Otherwise, combines historical filings (via company.get_filings) with current/recent
        filings (via get_current_filings) to ensure same-day filings are included.
        """
        # Normalize form_type: 'ALL'/'CORE' -> '' (empty string for edgartools, filter later)
        edgar_form_type = '' if form_type in ('ALL', 'CORE') else form_type

        # If no ticker specified, get latest filings across all companies
        if ticker is None:
            # Get CIK-to-ticker mapping for ticker lookups
            cik_to_ticker = self._get_cik_to_ticker_mapping()

            # Helper to convert edgar filing to domain model (no ticker override)
            def to_domain_filing_no_ticker(edgar_filing) -> Filing:
                filing_date = edgar_filing.filing_date
                if hasattr(filing_date, 'strftime'):
                    date_str = filing_date.strftime('%Y-%m-%d')
                elif hasattr(filing_date, 'date'):
                    date_str = filing_date.date().strftime('%Y-%m-%d')
                else:
                    date_str = str(filing_date)

                cik = int(edgar_filing.cik) if hasattr(edgar_filing, 'cik') and str(edgar_filing.cik).isdigit() else None
                if cik and cik in cik_to_ticker:
                    ticker_from_cik = cik_to_ticker[cik]
                elif cik:
                    ticker_from_cik = str(cik)
                else:
                    ticker_from_cik = 'UNKNOWN'

                return Filing(
                    ticker=ticker_from_cik,
                    form_type=edgar_filing.form,
                    filing_date=date_str,
                    accession_number=edgar_filing.accession_number,
                    sec_url=edgar_filing.url,
                    company_name=getattr(edgar_filing, 'company', None),
                    cik=str(cik) if cik else None
                )

            try:
                # Check TTL cache first (stale-while-revalidate pattern)
                cache_key = f"current_filings:{form_type}"
                cached_result, is_fresh = _current_filings_cache.get(cache_key, allow_stale=True)

                if is_fresh:
                    # Fresh cache hit - return immediately
                    current_filings = cached_result
                else:
                    # Stale or cache miss - try to fetch fresh data
                    # Clear edgartools' LRU cache to ensure we get fresh data when TTL expires
                    get_current_entries_on_page.cache_clear()

                    try:
                        # For CORE or ALL (without ticker): query core form types in parallel
                        # ALL without ticker would return too much noise (mutual fund forms, etc.)
                        if form_type in ('CORE', 'ALL'):
                            core_forms = ['10-K', '10-Q', '20-F', '6-K', '8-K', 'S-1', 'S-3', 'S-4']

                            def fetch_form(form: str):
                                try:
                                    return list(get_current_filings(form=form, page_size=50))
                                except Exception:
                                    return []

                            all_filings = []
                            # Set max_workers to 4 (instead of 8) to reduce parallel load on SEC.gov
                            with ThreadPoolExecutor(max_workers=4) as executor:
                                futures = {executor.submit(fetch_form, form): form for form in core_forms}
                                # Add 20 second timeout per future (fail faster than default 30s+retries)
                                try:
                                    for future in as_completed(futures, timeout=20):
                                        try:
                                            all_filings.extend(future.result(timeout=1))
                                        except (FutureTimeoutError, Exception):
                                            # Skip failed forms, continue with others
                                            pass
                                except FutureTimeoutError:
                                    # Overall timeout - return what we have so far
                                    pass
                            current_filings = all_filings
                        else:
                            current_filings = get_current_filings(form=edgar_form_type, page_size=200)

                        # Cache the fresh result
                        _current_filings_cache.set(cache_key, current_filings)
                    except Exception as fetch_err:
                        # Fetch failed - fall back to stale cache if available
                        if cached_result is not None:
                            import logging
                            logging.getLogger(__name__).info(
                                f"Returning stale cache for {form_type} (fetch failed: {fetch_err})"
                            )
                            current_filings = cached_result
                        else:
                            raise  # No stale cache to fall back on
            except (ReadTimeout, TimeoutException) as e:
                raise ValueError(
                    f"SEC.gov timeout: SEC EDGAR is responding slowly (>30s). "
                    f"This is likely due to high load on SEC servers. "
                    f"Try again in a moment. Error: {str(e)}"
                )
            except Exception as e:
                error_msg = str(e).lower()
                if "429" in error_msg or "too many requests" in error_msg:
                    raise ValueError(
                        f"Rate limited by SEC.gov: You've exceeded the 10 requests/second limit. "
                        f"Wait a moment and try again. Error: {str(e)}"
                    )
                raise ValueError(f"Failed to get latest filings: {str(e)}")

            # Convert to domain models
            result = [to_domain_filing_no_ticker(f) for f in current_filings]

            # Filter to core form types when 'CORE' is specified
            # SEC API returns variants (e.g., "S-4 POS" when querying "S-4"), so filter strictly
            if form_type == 'CORE':
                result = [f for f in result if f.form_type in CORE_FORM_TYPES]

            # Sort by date descending (most recent first)
            result.sort(key=lambda x: x.filing_date, reverse=True)

            # Deduplicate by (ticker, form_type, filing_date) - keep first for each
            seen = set()
            deduplicated = []
            for filing in result:
                key = (filing.ticker, filing.form_type, filing.filing_date)
                if key not in seen:
                    deduplicated.append(filing)
                    seen.add(key)

            return deduplicated

        # If ticker specified, get filings for that specific company
        company = Company(ticker)

        # Get historical filings (up to ~10 PM EST previous day)
        historical_filings = company.get_filings(form=edgar_form_type if edgar_form_type else None)

        # Get current/recent filings (same-day and recent)
        try:
            # Check TTL cache first (stale-while-revalidate pattern)
            cache_key = f"current_filings:{edgar_form_type}:{ticker}"
            cached_result, is_fresh = _current_filings_cache.get(cache_key, allow_stale=True)

            if is_fresh:
                # Fresh cache hit - return immediately
                current_for_company = cached_result
            else:
                # Stale or cache miss - try to fetch fresh data
                get_current_entries_on_page.cache_clear()
                try:
                    current_filings = get_current_filings(form=edgar_form_type, page_size=100)
                    # Filter for this company's CIK
                    current_for_company = [f for f in current_filings if f.cik == int(company.cik)]
                    # Cache the fresh result
                    _current_filings_cache.set(cache_key, current_for_company)
                except Exception:
                    # Fetch failed - fall back to stale cache if available
                    if cached_result is not None:
                        current_for_company = cached_result
                    else:
                        current_for_company = []
        except (ReadTimeout, TimeoutException) as e:
            # Log timeout but don't fail - historical filings still work
            import logging
            logging.getLogger(__name__).warning(
                f"SEC.gov timeout while fetching current filings for {ticker}: {str(e)}"
            )
            current_for_company = []
        except Exception as e:
            # Log other errors but don't fail - historical filings still work
            import logging
            logging.getLogger(__name__).warning(
                f"Failed to fetch current filings for {ticker}: {str(e)}"
            )
            current_for_company = []

        # Helper to convert edgar filing to domain model
        def to_domain_filing(edgar_filing, ticker: str) -> Filing:
            # Format filing date
            filing_date = edgar_filing.filing_date
            if hasattr(filing_date, 'strftime'):
                date_str = filing_date.strftime('%Y-%m-%d')
            elif hasattr(filing_date, 'date'):
                date_str = filing_date.date().strftime('%Y-%m-%d')
            else:
                date_str = str(filing_date)

            return Filing(
                ticker=ticker.upper(),
                form_type=edgar_filing.form,
                filing_date=date_str,
                accession_number=edgar_filing.accession_number,
                sec_url=edgar_filing.url,
                company_name=getattr(edgar_filing, 'company', None),
                cik=str(edgar_filing.cik) if hasattr(edgar_filing, 'cik') else None
            )

        # Convert all filings to domain models
        result = []

        # Add historical filings
        if historical_filings:
            for filing in historical_filings:
                result.append(to_domain_filing(filing, ticker))

        # Add current filings (deduplicate by accession number)
        seen_accessions = {f.accession_number for f in result}
        for filing in current_for_company:
            if filing.accession_number not in seen_accessions:
                result.append(to_domain_filing(filing, ticker))
                seen_accessions.add(filing.accession_number)

        # Filter to core form types when 'CORE' is specified
        if form_type == 'CORE':
            result = [f for f in result if f.form_type in CORE_FORM_TYPES]

        # Sort by date descending (most recent first)
        result.sort(key=lambda x: x.filing_date, reverse=True)

        # Deduplicate by filing_date - keep first (most recent accession) for each date
        # This handles cases where multiple filings exist for same date (amendments, etc.)
        seen_dates = set()
        deduplicated = []
        for filing in result:
            if filing.filing_date not in seen_dates:
                deduplicated.append(filing)
                seen_dates.add(filing.filing_date)

        return deduplicated

    def fetch(self, filing: Filing, format: str = "text", include_exhibits: bool = True) -> str:
        """Download filing content from SEC"""
        company = Company(filing.ticker)
        edgar_filing = company.get_filings(
            form=filing.form_type,
            accession_number=filing.accession_number
        )[0]

        # Download content in requested format
        if format == "markdown":
            content = edgar_filing.markdown()
        elif format == "html":
            content = edgar_filing.html()
        else:  # text
            content = edgar_filing.text()

        # Append exhibits (critical for 8-Ks where Exhibit 99.1 has the actual data)
        if include_exhibits:
            try:
                for exhibit in edgar_filing.exhibits:
                    if exhibit.document_type.startswith('EX-'):
                        ex_text = exhibit.text() if format != "html" else exhibit.content
                        if ex_text:
                            separator = "\n\n" + "=" * 70 + "\n"
                            separator += f"EXHIBIT: {exhibit.document_type} ({exhibit.document})\n"
                            separator += "=" * 70 + "\n\n"
                            content += separator + ex_text
            except Exception:
                pass  # If exhibits fail, return main document

        return content

    def get_latest(self, ticker: Optional[str], form_type: str, date: Optional[str] = None) -> Filing:
        """Get metadata for latest filing (or first filing >= date)

        If ticker is None, returns latest filing across all companies.
        """
        filings = self.list_available(ticker, form_type)

        if not filings:
            ticker_str = ticker if ticker else "any ticker"
            raise ValueError(f"No {form_type} filings found for {ticker_str}")

        # Filter by date if specified
        if date:
            filtered = [f for f in filings if f.filing_date >= date]
            if not filtered:
                ticker_str = ticker if ticker else "any ticker"
                raise ValueError(
                    f"No {form_type} filings found for {ticker_str} on or after {date}"
                )
            # Return the oldest filing that matches (closest to the target date)
            # filings are sorted newest-first, so filtered[-1] is the oldest match
            return filtered[-1]

        # Return most recent
        return filings[0]
