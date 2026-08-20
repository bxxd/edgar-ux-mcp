"""
Domain Models - Pure business entities

No external dependencies. These represent the core business concepts.
"""
from dataclasses import dataclass, field
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
class FilingDocument:
    """One document inside a filing's accession.

    A submission is a bundle, not a file. For most forms the primary document
    carries the substance; for some (13F-HR) it is only a cover page and the
    data lives in a sibling document.
    """
    sequence: str
    document_type: str
    document: str  # filename within the accession, e.g. "information_table.xml"
    description: Optional[str] = None
    url: Optional[str] = None
    is_primary: bool = False


@dataclass
class Holding:
    """One position line from a 13F information table"""
    issuer: str
    title_of_class: str
    cusip: str
    value: float  # USD, as reported
    shares: Optional[float] = None
    share_type: Optional[str] = None  # SH or PRN
    put_call: Optional[str] = None
    investment_discretion: Optional[str] = None
    ticker: Optional[str] = None  # resolved by edgartools where possible


@dataclass
class ThirteenFReport:
    """A 13F holdings report: cover-page totals AND the information table.

    cover_* come from the primary document; holdings come from the information
    table. Keeping both lets the caller verify they agree — a cover-page total
    reported without the table behind it is the exact failure this guards.
    """
    filing: Filing
    manager: str
    report_period: Optional[str]
    cover_total_holdings: Optional[int]  # tableEntryTotal
    cover_total_value: Optional[float]  # tableValueTotal
    holdings: list["Holding"]

    @property
    def table_total_value(self) -> float:
        return sum(h.value for h in self.holdings)

    @property
    def reconciles(self) -> bool:
        """Does the information table agree with the cover page?"""
        if self.cover_total_holdings is not None and self.cover_total_holdings != len(self.holdings):
            return False
        if self.cover_total_value is None:
            return True
        # Cover totals are reported in whole dollars; allow $1-per-row rounding
        return abs(self.table_total_value - self.cover_total_value) <= max(len(self.holdings), 1)


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
    document: Optional[str] = None  # non-primary document fetched from the accession
    # Substantive documents in the accession NOT included in this content.
    # Non-empty means this fetch is PARTIAL — the caller must be told.
    omitted_documents: list[FilingDocument] = field(default_factory=list)


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
