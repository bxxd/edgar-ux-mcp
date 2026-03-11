"""
Application Services - Use cases that orchestrate domain logic

These are the entry points to the core. They coordinate between
domain models and ports, but contain no infrastructure concerns.
"""
from typing import Optional, Literal
from edgar import Company

from .domain import Filing, FilingContent, SearchResult, CachedFiling
from .ports import FilingRepository, FilingFetcher, FilingSearcher


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
        force_refetch: bool = False
    ) -> FilingContent:
        """
        Fetch filing and cache it.

        Returns FilingContent with path, preview, and metadata.
        """
        # Get filing metadata
        filing = self.fetcher.get_latest(ticker, form_type, date)

        # Check if already cached (skip if force_refetch)
        cached_path = self.repository.get(ticker, form_type, filing.filing_date, format) if not force_refetch else None

        if not cached_path:
            # Download from SEC
            content = self.fetcher.fetch(filing, format, include_exhibits)

            # Save to cache
            filing_content = FilingContent(
                filing=filing,
                content=content,
                format=format,
                path=None,  # Will be set by repository
                size_bytes=len(content.encode('utf-8')),
                total_lines=content.count('\n') + 1
            )
            cached_path = self.repository.save(filing_content)

        # Return metadata only — no content in memory
        return FilingContent(
            filing=filing,
            content="",
            format=format,
            path=cached_path,
            size_bytes=cached_path.stat().st_size,
            total_lines=self.searcher.count_lines(cached_path)
        )


class ListFilingsService:
    """Use case: List available filings (both cached and from SEC)"""

    def __init__(
        self,
        repository: FilingRepository,
        fetcher: FilingFetcher
    ):
        self.repository = repository
        self.fetcher = fetcher

    def execute(self, ticker: Optional[str], form_type: str) -> tuple[list[Filing], list[CachedFiling]]:
        """
        List all available filings and which ones are cached.

        If ticker is None, returns latest filings across all companies.

        Returns:
            (available_filings, cached_filings)
        """
        # Get all available from SEC
        available = self.fetcher.list_available(ticker, form_type)

        # Get cached filings for this ticker/form (or all if ticker is None)
        cached = self.repository.list_all(ticker, form_type)

        return available, cached


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


class FinancialStatementsService:
    """Use case: Get structured financial statements from Entity Facts API"""

    def execute(
        self,
        ticker: str,
        statement_type: Literal["all", "income", "balance", "cash_flow"] = "all"
    ) -> dict:
        """
        Get multi-period financial statements using edgartools Entity Facts API.

        This uses edgartools' built-in caching (HTTP cache + LRU cache).
        No custom caching needed - edgartools handles it automatically.

        Args:
            ticker: Stock ticker (e.g., "TSLA", "AAPL")
            statement_type: Which statements to return

        Returns:
            Dict with statement data and metadata:
            {
                "company_name": str,
                "cik": str,
                "ticker": str,
                "statements": {
                    "income": MultiPeriodStatement or None,
                    "balance": MultiPeriodStatement or None,
                    "cash_flow": MultiPeriodStatement or None
                }
            }
        """
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

        return result
