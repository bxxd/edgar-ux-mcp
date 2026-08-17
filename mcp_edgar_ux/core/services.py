"""
Application Services - Use cases that orchestrate domain logic

These are the entry points to the core. They coordinate between
domain models and ports, but contain no infrastructure concerns.
"""
import logging
from datetime import datetime
from typing import Optional, Literal
from zoneinfo import ZoneInfo

from edgar import Company

from .domain import (
    Filing,
    FilingContent,
    FilingDocument,
    InsiderFiling,
    SearchResult,
    CachedFiling,
    ThirteenFReport,
)
from .ports import FilingRepository, FilingFetcher, FilingSearcher

logger = logging.getLogger(__name__)

EASTERN = ZoneInfo("America/New_York")


def _parse_since(since: str) -> datetime:
    """Parse a 'since' ISO timestamp. Naive timestamps are assumed US/Eastern (EDGAR-native)."""
    try:
        # 'Z' suffix normalized for the declared python floor (^3.10; native from 3.11)
        dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
    except ValueError as e:
        raise ValueError(f"since must be an ISO 8601 timestamp, got: {since!r}") from e
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=EASTERN)
    return dt


class FetchFilingService:
    """Use case: Fetch a SEC filing and cache it"""

    def __init__(
        self,
        repository: FilingRepository,
        fetcher: FilingFetcher,
        searcher: FilingSearcher
    ):
        self.repository = repository
        self.fetcher = fetcher
        self.searcher = searcher

    def execute(
        self,
        ticker: str,
        form_type: str,
        date: Optional[str] = None,
        format: str = "text",
        include_exhibits: bool = True,
        preview_lines: int = 50,
        force_refetch: bool = False,
        document: Optional[str] = None
    ) -> FilingContent:
        """
        Fetch filing and cache it.

        Returns FilingContent with path and metadata (content not loaded into memory).

        document: fetch a specific document from the accession instead of the
        primary one. A submission is a bundle; for some forms the primary
        document is only a cover page.
        """
        # Get filing metadata
        filing = self.fetcher.get_latest(ticker, form_type, date)

        # Check if already cached (skip if force_refetch)
        cached_path = self.repository.get(
            ticker, form_type, filing.filing_date, format, document
        ) if not force_refetch else None

        if cached_path:
            # Already cached — get metadata without reading content into memory
            total_lines = self.searcher.count_lines(cached_path)
        else:
            # Download from SEC
            content = self.fetcher.fetch(filing, format, include_exhibits, document)

            # Save to cache
            filing_content = FilingContent(
                filing=filing,
                content=content,
                format=format,
                path=None,  # Will be set by repository
                size_bytes=len(content.encode('utf-8')),
                total_lines=content.count('\n') + 1,
                document=document
            )
            cached_path = self.repository.save(filing_content)
            total_lines = filing_content.total_lines
            del content  # Release filing text (can be 10-160MB)

        # A fetch that silently returns part of a submission is the failure mode
        # this guards: a 13F cover page carries an authoritative-looking total
        # with not one issuer name behind it. Always report what was left behind.
        omitted = [] if document else self._omitted(filing, include_exhibits)

        # Return metadata only — caller uses path for content access
        return FilingContent(
            filing=filing,
            content="",  # Not loaded; use path with Read/Grep tools
            format=format,
            path=cached_path,
            size_bytes=cached_path.stat().st_size,
            total_lines=total_lines,
            document=document,
            omitted_documents=omitted
        )

    def _omitted(self, filing, include_exhibits: bool) -> list:
        """Documents in the accession this fetch did not return.

        Never fatal: a filing you already have in hand beats an error about
        the index, so a failure here degrades to 'nothing known omitted'.
        """
        try:
            return self.fetcher.omitted_documents(filing, include_exhibits)
        except Exception as e:  # noqa: BLE001 - advisory only
            logger.warning(
                f"Could not enumerate documents for {filing.accession_number}: {e}"
            )
            return []


class ListFilingsService:
    """Use case: List available filings (both cached and from SEC)"""

    def __init__(
        self,
        repository: FilingRepository,
        fetcher: FilingFetcher
    ):
        self.repository = repository
        self.fetcher = fetcher

    def execute(
        self,
        ticker: Optional[str],
        form_type: str,
        since: Optional[str] = None
    ) -> tuple[list[Filing], list[CachedFiling]]:
        """
        List all available filings and which ones are cached.

        If ticker is None, returns latest filings across all companies.
        If since is set (ISO timestamp, naive = US/Eastern), only filings
        accepted at/after that moment are returned — the acceptance-time axis
        for diffing "what landed after the last sweep ran".

        Returns:
            (available_filings, cached_filings)
        """
        # Get all available from SEC
        available = self.fetcher.list_available(ticker, form_type)

        if since:
            since_dt = _parse_since(since)
            available = [f for f in available if self._accepted_at_or_after(f, since_dt)]

        # Get cached filings for this ticker/form (or all if ticker is None)
        cached = self.repository.list_all(ticker, form_type)

        return available, cached

    @staticmethod
    def _accepted_at_or_after(filing: Filing, since_dt: datetime) -> bool:
        """Filter on acceptance time; fall back to filing date if acceptance unavailable."""
        if filing.acceptance_datetime:
            try:
                return datetime.fromisoformat(filing.acceptance_datetime) >= since_dt
            except ValueError:
                pass
        # No acceptance timestamp — keep if the filing DATE could plausibly be in range
        return filing.filing_date >= since_dt.date().isoformat()


class SearchFilingService:
    """Use case: Search for pattern within a filing"""

    def __init__(
        self,
        repository: FilingRepository,
        fetcher: FilingFetcher,
        searcher: FilingSearcher,
        fetch_service: FetchFilingService
    ):
        self.repository = repository
        self.fetcher = fetcher
        self.searcher = searcher
        self.fetch_service = fetch_service

    def execute(
        self,
        ticker: str,
        form_type: str,
        pattern: str,
        date: Optional[str] = None,
        format: str = "text",
        context_lines: int = 2,
        max_results: int = 20,
        offset: int = 0
    ) -> SearchResult:
        """
        Search for pattern in filing.

        Auto-fetches and caches filing if not already cached.
        """
        # Get filing metadata
        filing = self.fetcher.get_latest(ticker, form_type, date)

        # Ensure filing is cached
        cached_path = self.repository.get(ticker, form_type, filing.filing_date, format)

        if not cached_path:
            # Fetch and cache it first
            filing_content = self.fetch_service.execute(
                ticker, form_type, date, format, include_exhibits=True, preview_lines=0
            )
            cached_path = filing_content.path

        # Search in the cached file
        matches, total_count = self.searcher.search(
            cached_path,
            pattern,
            context_lines,
            max_results,
            offset
        )

        return SearchResult(
            filing=filing,
            pattern=pattern,
            matches=matches,
            total_matches=total_count,
            file_path=cached_path
        )


class ListDocumentsService:
    """Use case: List every document inside a filing's accession.

    The missing verb: fetch_filing returns the submission's PRIMARY document,
    and for some forms that is a cover page. This says what else is in there.
    """

    def __init__(self, fetcher: FilingFetcher):
        self.fetcher = fetcher

    def execute(
        self,
        ticker: str,
        form_type: str,
        date: Optional[str] = None
    ) -> tuple[Filing, list[FilingDocument]]:
        filing = self.fetcher.get_latest(ticker, form_type, date)
        return filing, self.fetcher.list_documents(filing)


class ThirteenFService:
    """Use case: 13F holdings — the information table, not just the cover page.

    13F is a HOLDER-side form: the filer is an institutional manager addressed
    by CIK, and the positions live in the information table document. Reading
    only the primary document yields a total with no issuers behind it.
    """

    def __init__(self, fetcher: FilingFetcher):
        self.fetcher = fetcher

    def execute(
        self,
        ticker: str,
        date: Optional[str] = None,
        form_type: str = "13F-HR"
    ) -> ThirteenFReport:
        filing = self.fetcher.get_latest(ticker, form_type, date)
        return self.fetcher.get_thirteenf(filing)


class InsiderActivityService:
    """Use case: Insider activity (Forms 3/4/5/144), series-aggregated per filer.

    The signal is the SERIES — single-filing reads miss sustained distribution
    (e.g. MP 5/12 + 5/27 + 6/03). Returns parsed filings; aggregation by filer
    happens at the formatting layer so the raw per-filing data stays available.
    """

    def __init__(self, fetcher: FilingFetcher):
        self.fetcher = fetcher

    def execute(self, ticker: str, days: int = 30) -> list[InsiderFiling]:
        if days < 1 or days > 365:
            raise ValueError("days must be between 1 and 365")
        return self.fetcher.get_insider_activity(ticker, days)


class FinancialStatementsService:
    """Use case: Get structured financial statements from Entity Facts API"""

    def execute(
        self,
        ticker: str,
        statement_type: Literal["all", "income", "balance", "cash_flow"] = "all"
    ) -> dict:
        """
        Get multi-period financial statements using edgartools Entity Facts API.

        Args:
            ticker: Stock ticker (e.g., "TSLA", "AAPL")
            statement_type: Which statements to return

        Returns:
            Dict with statement data and metadata
        """
        from edgar.entity.entity_facts import get_company_facts

        # Get company and facts
        company = Company(ticker)
        facts = company.get_facts()

        # Handle missing facts (common for warrants, units, rights, etc.)
        if facts is None:
            raise ValueError(
                f"No financial data available for {ticker}. "
                "This ticker may be a warrant, unit, or rights issue which do not file financial statements. "
                "Try the underlying common stock ticker instead."
            )

        # Build result
        result = {
            "company_name": company.name,
            "cik": company.cik,
            "ticker": ticker.upper(),
            "statements": {}
        }

        # Get requested statements
        if statement_type in ("all", "income"):
            result["statements"]["income"] = facts.income_statement()

        if statement_type in ("all", "balance"):
            result["statements"]["balance"] = facts.balance_sheet()

        if statement_type in ("all", "cash_flow"):
            result["statements"]["cash_flow"] = facts.cash_flow()

        # Clear edgartools' LRU cache for company facts to prevent memory accumulation.
        # Each company's facts JSON can be 5-20MB parsed; the lru_cache(maxsize=32) would
        # retain up to 32 of these indefinitely. We've already extracted what we need.
        get_company_facts.cache_clear()

        return result
