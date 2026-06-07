"""
Domain Models - Pure business entities

No external dependencies. These represent the core business concepts.
"""
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class Filing:
    """A SEC filing document"""
    ticker: str
    form_type: str
    filing_date: str  # YYYY-MM-DD format
    accession_number: str
    sec_url: str
    company_name: Optional[str] = None
    cik: Optional[str] = None
    acceptance_datetime: Optional[str] = None  # ISO 8601 with offset, US/Eastern (EDGAR-native)


@dataclass
class InsiderTransaction:
    """A single transaction line from an ownership form (Form 4/5)"""
    date: str  # YYYY-MM-DD transaction date
    code: str  # SEC transaction code (S=sale, P=purchase, M=exercise, etc.)
    description: str  # Human-readable code description
    shares: Optional[float]
    price: Optional[float]
    value: Optional[float]
    shares_owned_after: Optional[float]


@dataclass
class InsiderFiling:
    """A parsed ownership filing (Form 3/4/5/144) for insider activity analysis"""
    filer: str  # Reporting owner name
    position: Optional[str]  # e.g. "Chairman and CEO", "Director, 10% Owner"
    form_type: str
    filing_date: str
    acceptance_datetime: Optional[str]
    accession_number: str
    sec_url: str
    aff10b5One: Optional[bool]  # 10b5-1 plan checkbox (None if not present, e.g. Form 144)
    transactions: list[InsiderTransaction]
    footnotes: dict[str, str]  # footnote id -> text
    remarks: Optional[str] = None


@dataclass
class CachedFiling:
    """A filing cached on disk"""
    ticker: str
    form_type: str
    filing_date: str
    path: Path
    size_bytes: int
    format: str  # "text", "markdown", or "html"


@dataclass
class FilingContent:
    """Content of a downloaded filing"""
    filing: Filing
    content: str
    format: str  # "text", "markdown", or "html"
    path: Optional[Path]  # None before saving, set by repository after save
    size_bytes: int
    total_lines: int


@dataclass
class SearchMatch:
    """A search result match within a filing"""
    line_number: int
    line_content: str
    context_before: list[str]
    context_after: list[str]


@dataclass
class SearchResult:
    """Results from searching within a filing"""
    filing: Filing
    pattern: str
    matches: list[SearchMatch]
    total_matches: int
    file_path: Path
