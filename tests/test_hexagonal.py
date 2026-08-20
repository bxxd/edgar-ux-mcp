"""
Minimal tests for hexagonal architecture

Basic smoke tests to verify the architecture works.
"""
from pathlib import Path

from mcp_edgar_ux.core.domain import Filing, CachedFiling
from mcp_edgar_ux.container import Container


class TestDomainModels:
    """Test domain models are simple dataclasses."""

    def test_filing_creation(self):
        """Test Filing model."""
        filing = Filing(
            ticker="TSLA",
            form_type="10-K",
            filing_date="2024-01-30",
            accession_number="0001628280-24-002390",
            sec_url="https://sec.gov/...",
            company_name="Tesla, Inc.",
            cik="0001318605"
        )
        assert filing.ticker == "TSLA"
        assert filing.form_type == "10-K"

    def test_cached_filing_creation(self):
        """Test CachedFiling model."""
        cached = CachedFiling(
            ticker="TSLA",
            form_type="10-K",
            filing_date="2024-01-30",
            accession_number="0001318605-24-000009",
            path=Path("/tmp/test.txt"),
            size_bytes=1000,
            format="text"
        )
        assert cached.ticker == "TSLA"
        assert cached.size_bytes == 1000


class TestContainer:
    """Test dependency injection container."""

    def test_container_creates_all_services(self, tmp_path):
        """Test container initializes all dependencies."""
        container = Container(cache_dir=tmp_path, user_agent="test@example.com")

        # Check adapters exist
        assert container.cache is not None
        assert container.fetcher is not None
        assert container.searcher is not None

        # Check services exist
        assert container.fetch_filing is not None
        assert container.search_filing is not None
        assert container.list_filings is not None
        assert container.get_financials is not None


class TestMCPHandlers:
    """Test MCP handlers use the container."""

    def test_handlers_initialization(self, tmp_path):
        """Test MCP handlers can be created."""
        from mcp_edgar_ux.adapters.mcp.handlers import MCPHandlers

        container = Container(cache_dir=tmp_path, user_agent="test@example.com")
        handlers = MCPHandlers(container)

        assert handlers.container is container


class TestCoreFormTypes:
    """Test CORE_FORM_TYPES whitelist."""

    def test_core_form_types_contains_essential_forms(self):
        """Test essential forms are in CORE_FORM_TYPES."""
        from mcp_edgar_ux.adapters.edgar import CORE_FORM_TYPES

        # Must have annual/quarterly
        assert '10-K' in CORE_FORM_TYPES
        assert '10-Q' in CORE_FORM_TYPES

        # Must have current reports
        assert '8-K' in CORE_FORM_TYPES

        # Must have registration statements, including the automatic-shelf
        # variant (S-3ASR) — a distinct form type, not matched by 'S-3'.
        assert 'S-1' in CORE_FORM_TYPES
        assert 'S-3' in CORE_FORM_TYPES
        assert 'S-3ASR' in CORE_FORM_TYPES

        # Foreign-issuer periodic reports. 6-K is deliberately IN core (the
        # trawl protocol relies on it); the tool-doc list abbreviates and
        # omits it, which is a doc bug, not a behavior one.
        assert '6-K' in CORE_FORM_TYPES

    def test_core_form_types_excludes_noise(self):
        """Test noise forms are NOT in CORE_FORM_TYPES."""
        from mcp_edgar_ux.adapters.edgar import CORE_FORM_TYPES

        # Form 4 (insider trading) should NOT be in CORE
        assert '4' not in CORE_FORM_TYPES
        assert '3' not in CORE_FORM_TYPES
        assert '5' not in CORE_FORM_TYPES

        # Ownership filings excluded as noise — reachable via ALL or by form.
        assert 'SC 13D' not in CORE_FORM_TYPES
        assert 'SC 13G' not in CORE_FORM_TYPES

        # Proxy statements excluded. NB: this is a real coverage gap for
        # contested votes (see the SEER 2026-07 miss) — the documented
        # workaround is a per-ticker form_type="ALL" sweep. Asserted here so
        # the exclusion stays a deliberate choice rather than a silent one.
        assert 'DEF 14A' not in CORE_FORM_TYPES
        assert 'DEFA14A' not in CORE_FORM_TYPES

