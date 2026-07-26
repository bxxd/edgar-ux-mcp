"""
EDGAR Adapter

Implements FilingFetcher port using edgartools library.
"""
import logging
import re
import time
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo
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

from ..core.domain import Filing, InsiderFiling, InsiderTransaction
from ..core.ports import FilingFetcher

logger = logging.getLogger(__name__)

# EDGAR business semantics are US/Eastern — acceptance times normalized to ET
EASTERN = ZoneInfo("America/New_York")


def _to_eastern_iso(value) -> Optional[str]:
    """Normalize an acceptance timestamp (datetime or ISO string, any tz) to ET ISO 8601."""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=EASTERN)  # current feed timestamps are ET
    return value.astimezone(EASTERN).isoformat(timespec='seconds')


def _safe_float(value) -> Optional[float]:
    """Coerce a pandas cell to float; footnote refs / NaN / None -> None"""
    try:
        f = float(value)
        return None if f != f else f  # NaN check
    except (TypeError, ValueError):
        return None


def _feed_accepted_column(current_filings) -> list:
    """Extract the 'accepted' timestamps from a CurrentFilings pyarrow table.

    edgartools parses acceptance time from the getcurrent atom feed into its
    table but drops it when yielding Filing objects — read the column directly.
    Order matches iteration order (both walk the current page).
    """
    if 'accepted' in current_filings.data.column_names:
        return current_filings.data['accepted'].to_pylist()
    return [None] * current_filings.data.num_rows


def _disable_edgartools_http_cache():
    """
    Disable edgartools' Hishel HTTP response cache.

    edgartools caches full SEC filing HTTP responses (up to 161MB each) in ~/.edgar/_tcache/
    using Hishel-File mode. When cached responses are accessed, they're deserialized into
    Python memory. With "/Archives/edgar/data": True (cache forever), this causes unbounded
    memory growth — we observed 1.8GB RSS from ~5000 cached responses.

    We have our own disk cache in /var/idio-mcp-cache/sec-filings/, so edgartools' HTTP
    cache is pure duplication. Disable it but keep rate limiting.
    """
    import edgar.httpclient as httpclient
    old_mgr = httpclient.HTTP_MGR
    new_mgr = httpclient.get_http_mgr(
        cache_enabled=False,
        request_per_sec_limit=httpclient.get_edgar_rate_limit_per_sec()
    )
    httpclient.HTTP_MGR = new_mgr
    old_mgr.close()
    logger.info("Disabled edgartools HTTP cache (rate limiting preserved)")


# Disable edgartools HTTP cache on module load — we have our own disk cache
_disable_edgartools_http_cache()


# Time-based cache for current filings to avoid hammering SEC.gov
# Cache TTL: 90 seconds (balances freshness vs. load)
class TTLCache:
    """Simple time-to-live cache with stale-while-revalidate support.

    Stores only lightweight domain objects (Filing dataclasses), not raw
    edgartools objects, to avoid retaining heavy parsed state in memory.
    """
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
# Fresh: 90s, Stale-acceptable: 1 hour (serve during SEC.gov slowness)
_current_filings_cache = TTLCache(ttl_seconds=90, stale_ttl_seconds=3600)

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
            def to_domain_filing_no_ticker(edgar_filing, accepted=None) -> Filing:
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
                    cik=str(cik) if cik else None,
                    acceptance_datetime=_to_eastern_iso(accepted)
                )

            try:
                # Check TTL cache first (stale-while-revalidate pattern)
                # Cache stores domain Filing objects (lightweight), not raw edgartools objects
                cache_key = f"current_filings:{form_type}"
                cached_result, is_fresh = _current_filings_cache.get(cache_key, allow_stale=True)

                if is_fresh:
                    # Fresh cache hit - already domain models, return directly
                    return cached_result
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
                                """Returns list of (edgar_filing, accepted_datetime) pairs"""
                                try:
                                    cf = get_current_filings(form=form, page_size=50)
                                    return list(zip(list(cf), _feed_accepted_column(cf)))
                                except Exception:
                                    return []

                            raw_filings = []
                            # Set max_workers to 4 (instead of 8) to reduce parallel load on SEC.gov
                            with ThreadPoolExecutor(max_workers=4) as executor:
                                futures = {executor.submit(fetch_form, form): form for form in core_forms}
                                # Add 20 second timeout per future (fail faster than default 30s+retries)
                                try:
                                    for future in as_completed(futures, timeout=20):
                                        try:
                                            raw_filings.extend(future.result(timeout=1))
                                        except (FutureTimeoutError, Exception):
                                            # Skip failed forms, continue with others
                                            pass
                                except FutureTimeoutError:
                                    # Overall timeout - return what we have so far
                                    pass
                        else:
                            cf = get_current_filings(form=edgar_form_type, page_size=200)
                            raw_filings = list(zip(list(cf), _feed_accepted_column(cf)))
                            del cf

                        # Convert to lightweight domain models BEFORE caching
                        # This ensures raw edgartools objects (with parsed HTML, DataFrames, etc.)
                        # are not retained in memory via the TTL cache
                        result = [to_domain_filing_no_ticker(f, acc) for f, acc in raw_filings]
                        del raw_filings  # Release edgartools objects immediately

                        # Filter to core form types when 'CORE' is specified
                        if form_type == 'CORE':
                            result = [f for f in result if f.form_type in CORE_FORM_TYPES]

                        # Sort by date descending, then acceptance time, so the
                        # now-possible multiple filings per date have a stable,
                        # meaningful order instead of an arbitrary one.
                        result.sort(
                            key=lambda x: (x.filing_date, x.acceptance_datetime or ''),
                            reverse=True,
                        )

                        # Deduplicate by accession number, same as the per-ticker
                        # path. The old (ticker, form_type, filing_date) key was
                        # less destructive than keying on date alone, but still
                        # dropped a second same-form filing on the same day —
                        # e.g. two 8-Ks from one company, which is routine.
                        seen = set()
                        deduplicated = []
                        for filing in result:
                            if filing.accession_number not in seen:
                                deduplicated.append(filing)
                                seen.add(filing.accession_number)

                        # Cache the processed domain models
                        _current_filings_cache.set(cache_key, deduplicated)
                        return deduplicated
                    except Exception as fetch_err:
                        # Fetch failed - fall back to stale cache if available
                        if cached_result is not None:
                            logger.info(
                                f"Returning stale cache for {form_type} (fetch failed: {fetch_err})"
                            )
                            return cached_result
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

        # If ticker specified, get filings for that specific company
        company = Company(ticker)

        # Get historical filings (up to ~10 PM EST previous day)
        historical_filings = company.get_filings(form=edgar_form_type if edgar_form_type else None)

        # Helper to convert edgar filing to domain model
        def to_domain_filing(edgar_filing, ticker: str, accepted=None) -> Filing:
            filing_date = edgar_filing.filing_date
            if hasattr(filing_date, 'strftime'):
                date_str = filing_date.strftime('%Y-%m-%d')
            elif hasattr(filing_date, 'date'):
                date_str = filing_date.date().strftime('%Y-%m-%d')
            else:
                date_str = str(filing_date)

            # EntityFiling carries acceptance_datetime from the submissions JSON (free);
            # current-feed filings get it passed in from the table's 'accepted' column
            if accepted is None:
                accepted = getattr(edgar_filing, 'acceptance_datetime', None)

            return Filing(
                ticker=ticker.upper(),
                form_type=edgar_filing.form,
                filing_date=date_str,
                accession_number=edgar_filing.accession_number,
                sec_url=edgar_filing.url,
                company_name=getattr(edgar_filing, 'company', None),
                cik=str(edgar_filing.cik) if hasattr(edgar_filing, 'cik') else None,
                acceptance_datetime=_to_eastern_iso(accepted)
            )

        # Convert historical filings to domain models
        result = []
        if historical_filings:
            for filing in historical_filings:
                result.append(to_domain_filing(filing, ticker))

        # Get current/recent filings (same-day and recent)
        # TTL cache stores domain Filing objects, not raw edgartools objects
        current_domain_filings = []
        try:
            cache_key = f"current_filings:{edgar_form_type}:{ticker}"
            cached_result, is_fresh = _current_filings_cache.get(cache_key, allow_stale=True)

            if is_fresh:
                # Fresh cache hit - already domain models
                current_domain_filings = cached_result
            else:
                # Stale or cache miss - try to fetch fresh data
                get_current_entries_on_page.cache_clear()
                try:
                    cf = get_current_filings(form=edgar_form_type, page_size=100)
                    raw_current = list(zip(list(cf), _feed_accepted_column(cf)))
                    del cf
                    # Filter for this company's CIK and convert to domain models
                    company_cik = int(company.cik)
                    current_domain_filings = [
                        to_domain_filing(f, ticker, acc)
                        for f, acc in raw_current if f.cik == company_cik
                    ]
                    del raw_current  # Release edgartools objects
                    # Cache domain models
                    _current_filings_cache.set(cache_key, current_domain_filings)
                except Exception:
                    # Fetch failed - fall back to stale cache if available
                    if cached_result is not None:
                        current_domain_filings = cached_result
        except (ReadTimeout, TimeoutException) as e:
            logger.warning(f"SEC.gov timeout fetching current filings for {ticker}: {e}")
        except Exception as e:
            logger.warning(f"Failed to fetch current filings for {ticker}: {e}")

        # Add current filings (deduplicate by accession number)
        seen_accessions = {f.accession_number for f in result}
        for filing in current_domain_filings:
            if filing.accession_number not in seen_accessions:
                result.append(filing)
                seen_accessions.add(filing.accession_number)

        # Filter to core form types when 'CORE' is specified
        if form_type == 'CORE':
            result = [f for f in result if f.form_type in CORE_FORM_TYPES]

        # Sort by date descending, then acceptance time (see the no-ticker path):
        # multiple filings per date are now preserved, so intra-date order matters.
        result.sort(
            key=lambda x: (x.filing_date, x.acceptance_datetime or ''),
            reverse=True,
        )

        # Deduplicate by accession number — the SEC's unique id for a filing.
        #
        # This used to key on filing_date alone, which silently kept only ONE
        # filing per date no matter how many distinct ones a company submitted.
        # Companies routinely file several on one day (VKTX 2026-02-11: 10-K +
        # an 8-K carrying results; 2026-04-30: 10-Q + a SCHEDULE 13G), so the
        # survivor was whichever sorted first and the rest vanished with no
        # marker. That made list_filings unusable for establishing that
        # something was NOT filed, and it hit every form_type — CORE included,
        # whenever the colliding filings were both core forms.
        #
        # Accession is the correct key: distinct filings always differ, and a
        # genuine duplicate (the same filing reached via both the historical and
        # the current feed) still collapses.
        seen_accession_numbers = set()
        deduplicated = []
        for filing in result:
            if filing.accession_number not in seen_accession_numbers:
                deduplicated.append(filing)
                seen_accession_numbers.add(filing.accession_number)

        return deduplicated

    def fetch(self, filing: Filing, format: str = "text", include_exhibits: bool = True) -> str:
        """Download filing content from SEC"""
        company = Company(filing.ticker)
        edgar_filing = company.get_filings(
            form=filing.form_type,
            accession_number=filing.accession_number
        )[0]

        # Download content in requested format
        if format == "xml":
            # Raw XML passthrough — lossless for Forms 3/4/5/144 where the text
            # render drops decisive fields (aff10b5One checkbox, footnotes)
            content = edgar_filing.xml()
            if not content:
                raise ValueError(
                    f"No XML document for {filing.form_type} {filing.accession_number}. "
                    f"format='xml' applies to XML-primary filings (Forms 3/4/5/144)."
                )
            return content  # no exhibits for raw XML
        elif format == "markdown":
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

    def get_insider_activity(self, ticker: str, days: int) -> list[InsiderFiling]:
        """Fetch and parse ownership filings (Forms 3/4/5/144) for the last N days.

        Forms 3/4/5 are parsed from raw XML (single fetch per filing) so the
        aff10b5One checkbox and footnotes survive — the text render drops both.
        """
        company = Company(ticker)
        cutoff = (datetime.now(EASTERN) - timedelta(days=days)).date().isoformat()
        filings = company.get_filings(
            form=["3", "3/A", "4", "4/A", "5", "5/A", "144", "144/A"],
            filing_date=f"{cutoff}:"
        )

        results: list[InsiderFiling] = []
        for f in (filings or []):
            try:
                acceptance = _to_eastern_iso(getattr(f, 'acceptance_datetime', None))
                if f.form.replace("/A", "") == "144":
                    results.append(self._parse_form144(f, acceptance))
                else:
                    results.append(self._parse_ownership_form(f, acceptance))
            except Exception as e:
                logger.warning(f"Failed to parse {f.form} {f.accession_number} for {ticker}: {e}")

        # Newest first; acceptance as secondary key keeps same-day series stable
        results.sort(key=lambda r: (r.filing_date, r.acceptance_datetime or ''), reverse=True)
        return results

    def _parse_ownership_form(self, edgar_filing, acceptance: Optional[str]) -> InsiderFiling:
        """Parse Form 3/4/5 from raw XML (obj() would re-download the XML)"""
        # NB: Form4.from_xml is broken in edgartools 4.29 (double-construction:
        # subclass parse_xml returns an instance, from_xml then does cls(**instance)).
        # Ownership.from_xml works and form-specific behavior dispatches on the
        # parsed form field.
        from edgar.ownership import Ownership

        xml = edgar_filing.xml()
        if not xml:
            raise ValueError("no XML document")

        base_form = edgar_filing.form.replace("/A", "")
        ownership = Ownership.from_xml(xml)

        # 10b5-1 checkbox — edgartools drops this field entirely; it's the
        # discretionary-vs-planned distinction so parse it from the raw XML
        m = re.search(r"<aff10b5One>\s*([^<\s]+)\s*</aff10b5One>", xml)
        aff10b5one = (m.group(1).lower() in ("1", "true")) if m else None

        # Read per-transaction rows from the ownership tables directly.
        # NOT ownership.to_dataframe() — its summary collapses per-transaction
        # dates and post-transaction holdings to filing-level values (every row
        # would show the reporting-period date and the FINAL post position).
        transactions = []
        if base_form != "3":  # Form 3 is initial holdings, no transactions
            transactions.extend(
                self._table_transactions(ownership.non_derivative_table.transactions)
            )
            if ownership.derivative_table is not None and ownership.derivative_table.has_transactions:
                transactions.extend(
                    self._table_transactions(ownership.derivative_table.transactions, derivative=True)
                )

        footnotes = (
            ownership.footnotes.summary()["footnote"].to_dict()
            if len(ownership.footnotes) else {}
        )

        return InsiderFiling(
            filer=ownership.insider_name or "UNKNOWN",
            position=ownership.position,
            form_type=edgar_filing.form,
            filing_date=str(edgar_filing.filing_date),
            acceptance_datetime=acceptance,
            accession_number=edgar_filing.accession_number,
            sec_url=edgar_filing.url,
            aff10b5One=aff10b5one,
            transactions=transactions,
            footnotes=footnotes,
            remarks=ownership.remarks or None,
        )

    @staticmethod
    def _table_transactions(transactions, derivative: bool = False) -> list[InsiderTransaction]:
        """Per-transaction rows from a (Non)DerivativeTransactions table.

        Both tables carry Date/Code/Shares/Price/Remaining/footnotes per
        transaction; derivative rows add Security/Underlying.
        """
        if transactions is None or transactions.empty:
            return []

        rows = []
        for _, row in transactions.data.iterrows():
            desc = str(row.get("TransactionType") or "").replace("_", " ").title()
            if derivative:
                security = str(row.get("Security") or "").strip()
                desc = f"{desc or 'Derivative'} ({security})" if security else f"{desc or 'Derivative'}"
            elif not desc:
                desc = str(row.get("Code", ""))
            fn = str(row.get("footnotes") or "").strip()
            if fn:
                desc = f"{desc} [{fn}]"

            shares = _safe_float(row.get("Shares"))
            price = _safe_float(row.get("Price"))
            rows.append(InsiderTransaction(
                date=str(row.get("Date", "")),
                code=str(row.get("Code", "")),
                description=desc,
                shares=shares,
                price=price,
                value=round(shares * price, 2) if shares is not None and price is not None else None,
                shares_owned_after=_safe_float(row.get("Remaining")),
            ))
        return rows

    def _parse_form144(self, edgar_filing, acceptance: Optional[str]) -> InsiderFiling:
        """Parse Form 144 (proposed sale notice)"""
        form144 = edgar_filing.obj()

        approx_date = getattr(form144, 'approx_sale_date', None)
        transactions = [InsiderTransaction(
            date=str(approx_date) if approx_date else str(edgar_filing.filing_date),
            code="144",
            description="Proposed sale (Form 144 notice)",
            shares=_safe_float(getattr(form144, 'units_to_be_sold', None)),
            price=None,
            value=_safe_float(getattr(form144, 'market_value', None)),
            shares_owned_after=None,
        )]

        relationships = getattr(form144, 'relationships', None)
        if isinstance(relationships, (list, tuple)):
            relationships = ", ".join(str(r) for r in relationships) or None

        return InsiderFiling(
            filer=getattr(form144, 'person_selling', None) or "UNKNOWN",
            position=relationships,
            form_type=edgar_filing.form,
            filing_date=str(edgar_filing.filing_date),
            acceptance_datetime=acceptance,
            accession_number=edgar_filing.accession_number,
            sec_url=edgar_filing.url,
            aff10b5One=None,  # Form 144 has no 10b5-1 checkbox
            transactions=transactions,
            footnotes={},
            remarks=getattr(form144, 'remarks', None) or None,
        )

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
