"""
Core - Domain logic and ports

This package contains:
- domain.py: Pure domain models
- ports.py: Port interfaces (abstractions for external dependencies)
- services.py: Application services (use cases)
"""
from .domain import (
    Filing,
    CachedFiling,
    FilingContent,
    InsiderFiling,
    InsiderTransaction,
    SearchMatch,
    SearchResult
)
from .ports import FilingRepository, FilingFetcher, FilingSearcher
from .services import (
    FetchFilingService,
    ListFilingsService,
    SearchFilingService,
    FinancialStatementsService,
    InsiderActivityService
)

__all__ = [
    # Domain models
    "Filing",
    "CachedFiling",
    "FilingContent",
    "InsiderFiling",
    "InsiderTransaction",
    "SearchMatch",
    "SearchResult",
    # Ports
    "FilingRepository",
    "FilingFetcher",
    "FilingSearcher",
    # Services
    "FetchFilingService",
    "ListFilingsService",
    "SearchFilingService",
    "FinancialStatementsService",
    "InsiderActivityService"
]
