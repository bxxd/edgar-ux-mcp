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
    FilingDocument,
    Holding,
    InsiderFiling,
    InsiderTransaction,
    SearchMatch,
    SearchResult,
    ThirteenFReport
)
from .ports import FilingRepository, FilingFetcher, FilingSearcher
from .services import (
    FetchFilingService,
    ListFilingsService,
    ListDocumentsService,
    SearchFilingService,
    FinancialStatementsService,
    InsiderActivityService,
    ThirteenFService
)

__all__ = [
    # Domain models
    "Filing",
    "CachedFiling",
    "FilingContent",
    "FilingDocument",
    "Holding",
    "InsiderFiling",
    "InsiderTransaction",
    "SearchMatch",
    "SearchResult",
    "ThirteenFReport",
    # Ports
    "FilingRepository",
    "FilingFetcher",
    "FilingSearcher",
    # Services
    "FetchFilingService",
    "ListFilingsService",
    "ListDocumentsService",
    "SearchFilingService",
    "FinancialStatementsService",
    "InsiderActivityService",
    "ThirteenFService"
]
