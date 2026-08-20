"""
Tests for accession-document access, CIK addressing, and 13F holdings.

Covers the failure these were built for: fetch_filing returning a 13F cover
page that looks like a complete filing, and a ticker-keyed interface unable to
address a 13F filer that has no ticker.

Pure unit tests — no network.
"""
import pytest

from mcp_edgar_ux.adapters.edgar import EdgarAdapter, normalize_identifier
from mcp_edgar_ux.adapters.filesystem import FilesystemCache
from mcp_edgar_ux.core.domain import Filing, FilingContent, FilingDocument, Holding, ThirteenFReport
from mcp_edgar_ux.formatters import format_fetch_filing


def _filing(ticker="NVDA", form_type="13F-HR"):
    return Filing(
        ticker=ticker,
        form_type=form_type,
        filing_date="2026-08-14",
        accession_number="0001045810-26-000065",
        sec_url="https://sec.gov/...",
    )


class TestNormalizeIdentifier:
    """A 13F filer is a holder, not an issuer — it has a CIK and no ticker."""

    def test_ticker_passes_through_uppercased(self):
        assert normalize_identifier("nvda") == ("NVDA", None)

    @pytest.mark.parametrize("raw", ["1082621", "0001082621", "CIK0001082621", "cik-1082621"])
    def test_cik_forms_all_resolve_to_same_label(self, raw):
        assert normalize_identifier(raw) == ("CIK0001082621", 1082621)

    def test_cik_label_cannot_collide_with_a_ticker(self):
        label, cik = normalize_identifier("1082621")
        assert cik == 1082621
        assert label.startswith("CIK")

    def test_empty_identifier_rejected(self):
        with pytest.raises(ValueError):
            normalize_identifier("  ")


class TestOmittedDocuments:
    """The rule that decides whether a fetch was partial.

    Must fire on a 13F information table and stay silent on the XBRL/graphics
    packaging that every 10-K and 8-K carries — otherwise it is noise and gets
    ignored, which is the same as not existing.
    """

    def _adapter_with(self, documents):
        adapter = EdgarAdapter.__new__(EdgarAdapter)  # skip set_identity/network
        adapter.list_documents = lambda filing: documents
        return adapter

    def test_13f_information_table_is_reported_as_omitted(self):
        adapter = self._adapter_with([
            FilingDocument("1", "13F-HR", "primary_doc.xml", "", is_primary=True),
            FilingDocument("2", "INFORMATION TABLE", "56904.xml", "INFORMATION TABLE FOR FORM 13F"),
        ])
        omitted = adapter.omitted_documents(
            _filing(), format="text", include_exhibits=True
        )
        assert [d.document for d in omitted] == ["56904.xml"]

    def test_10k_xbrl_and_exhibits_do_not_trigger_a_warning(self):
        adapter = self._adapter_with([
            FilingDocument("1", "10-K", "tsla-10k.htm", "FORM 10-K", is_primary=True),
            FilingDocument("2", "EX-31.3", "ex31-3.htm", "EXHIBIT 31.3"),
            FilingDocument("4", "EX-101.SCH", "tsla.xsd", "XBRL TAXONOMY EXTENSION SCHEMA"),
            FilingDocument("8", "HTML", "R1.htm", "IDEA: XBRL DOCUMENT"),
            FilingDocument("9", "CSS", "report.css", "IDEA: XBRL DOCUMENT"),
            FilingDocument("16", "ZIP", "xbrl.zip", "IDEA: XBRL DOCUMENT"),
        ])
        assert adapter.omitted_documents(
            _filing(form_type="10-K"), format="text", include_exhibits=True
        ) == []

    def test_8k_graphics_do_not_trigger_a_warning(self):
        adapter = self._adapter_with([
            FilingDocument("1", "8-K", "tsla.htm", "8-K", is_primary=True),
            FilingDocument("2", "EX-99.1", "exhibit991.htm", "EX-99.1"),
            FilingDocument("6", "GRAPHIC", "exhibit991001.jpg", ""),
        ])
        assert adapter.omitted_documents(
            _filing(form_type="8-K"), format="text", include_exhibits=True
        ) == []

    def test_exhibits_count_as_omitted_when_not_appended(self):
        adapter = self._adapter_with([
            FilingDocument("1", "8-K", "tsla.htm", "8-K", is_primary=True),
            FilingDocument("2", "EX-99.1", "exhibit991.htm", "EX-99.1"),
        ])
        omitted = adapter.omitted_documents(
            _filing(form_type="8-K"), format="text", include_exhibits=False
        )
        assert [d.document for d in omitted] == ["exhibit991.htm"]

    def test_xml_passthrough_reports_exhibits_as_omitted(self):
        """format='xml' returns the primary XML document ALONE.

        fetch() takes an early return on that path and never reaches the
        exhibit loop, so asking for exhibits does not produce them. Reporting
        them as in hand is a false all-clear from the mechanism built to
        prevent false all-clears.
        """
        adapter = self._adapter_with([
            FilingDocument("1", "144", "primary_doc.xml", "FORM 144", is_primary=True),
            FilingDocument("2", "EX-99.1", "exhibit991.htm", "EX-99.1"),
        ])
        omitted = adapter.omitted_documents(
            _filing(form_type="144"), format="xml", include_exhibits=True
        )
        assert [d.document for d in omitted] == ["exhibit991.htm"]

    @pytest.mark.parametrize("fmt", ["text", "markdown", "html"])
    def test_rendered_formats_still_count_appended_exhibits_as_in_hand(self, fmt):
        adapter = self._adapter_with([
            FilingDocument("1", "8-K", "tsla.htm", "8-K", is_primary=True),
            FilingDocument("2", "EX-99.1", "exhibit991.htm", "EX-99.1"),
        ])
        assert adapter.omitted_documents(
            _filing(form_type="8-K"), format=fmt, include_exhibits=True
        ) == []

    def test_a_13f_information_table_fires_on_the_xml_path_too(self):
        adapter = self._adapter_with([
            FilingDocument("1", "13F-HR", "primary_doc.xml", "", is_primary=True),
            FilingDocument("2", "INFORMATION TABLE", "56904.xml", "INFORMATION TABLE FOR FORM 13F"),
        ])
        omitted = adapter.omitted_documents(
            _filing(), format="xml", include_exhibits=True
        )
        assert [d.document for d in omitted] == ["56904.xml"]


class TestThirteenFReconciliation:
    """Cover-page totals must be checked against the table, never reported alone."""

    def _report(self, holdings, cover_count, cover_value):
        return ThirteenFReport(
            filing=_filing(),
            manager="NVIDIA CORP",
            report_period="2026-06-30",
            cover_total_holdings=cover_count,
            cover_total_value=cover_value,
            holdings=holdings,
        )

    def test_matching_table_reconciles(self):
        holdings = [Holding("INTEL CORP", "COM", "458140100", 100.0),
                    Holding("SPACE EXPLORATION TECHN CORP", "CLASS A", "84615Q103", 50.0)]
        assert self._report(holdings, 2, 150.0).reconciles

    def test_value_mismatch_does_not_reconcile(self):
        holdings = [Holding("INTEL CORP", "COM", "458140100", 100.0)]
        assert not self._report(holdings, 1, 63_439_974_569.0).reconciles

    def test_count_mismatch_does_not_reconcile(self):
        holdings = [Holding("INTEL CORP", "COM", "458140100", 100.0)]
        assert not self._report(holdings, 8, 100.0).reconciles

    def test_empty_table_against_a_real_cover_total_does_not_reconcile(self):
        """The exact trap: a $63B cover total with nothing behind it."""
        assert not self._report([], 8, 63_439_974_569.0).reconciles

    def test_table_total_sums_holdings(self):
        holdings = [Holding("A", "COM", "1", 10.0), Holding("B", "COM", "2", 5.5)]
        assert self._report(holdings, 2, 15.5).table_total_value == 15.5


class TestDocumentCachePaths:
    """Named documents must not be mistaken for filings by the cache index."""

    ACC = "0001045810-26-000065"

    def test_primary_document_is_addressed_by_date_and_accession(self, tmp_path):
        cache = FilesystemCache(tmp_path)
        path = cache._get_path("NVDA", "13F-HR", "2026-08-14", self.ACC, "text")
        assert path.name == f"2026-08-14-{self.ACC}.txt"

    def test_named_document_goes_under_an_accession_directory(self, tmp_path):
        cache = FilesystemCache(tmp_path)
        path = cache._get_path(
            "NVDA", "13F-HR", "2026-08-14", self.ACC, "xml", "56904.xml"
        )
        assert path.name == "56904.xml"
        assert path.parent.name == f"2026-08-14-{self.ACC}"

    def test_cached_documents_do_not_appear_as_filings(self, tmp_path):
        cache = FilesystemCache(tmp_path)
        cache.save(FilingContent(
            filing=_filing(),
            content="<informationTable/>",
            format="xml",
            path=None,
            size_bytes=19,
            total_lines=1,
            document="56904.xml",
        ))
        # list_all reads a filename stem as the filing date — a document stored
        # flat would surface as a filing dated "2026-08-14__56904"
        assert cache.list_all(ticker="NVDA") == []

    def test_document_path_traversal_is_stripped(self, tmp_path):
        cache = FilesystemCache(tmp_path)
        path = cache._get_path(
            "NVDA", "13F-HR", "2026-08-14", self.ACC, "xml", "../../../etc/passwd"
        )
        assert path.name == "passwd"
        assert tmp_path in path.parents


class TestPartialFetchFormatting:
    """A partial fetch has to announce itself in the output the agent reads."""

    def _result(self, form_type="13F-HR", omitted=None):
        return {
            "success": True,
            "path": "/cache/NVDA/13F-HR/2026-08-14.txt",
            "cached": False,
            "document": None,
            "partial": bool(omitted),
            "omitted_documents": omitted or [],
            "metadata": {
                "company": "NVIDIA CORP", "ticker": "NVDA", "form_type": form_type,
                "filing_date": "2026-08-14", "accession_number": "0001045810-26-000065",
                "sec_url": "https://sec.gov/...", "format": "text",
                "size_bytes": 8008, "total_lines": 67,
            },
        }

    def test_13f_cover_page_fetch_is_marked_partial(self):
        out = format_fetch_filing(self._result(omitted=[{
            "sequence": "2", "document_type": "INFORMATION TABLE",
            "document": "information_table.xml", "description": "INFORMATION TABLE",
        }]))
        assert "PARTIAL" in out
        assert "information_table.xml" in out
        assert "thirteenf_holdings" in out
        assert 'document="2"' in out

    def test_complete_fetch_says_nothing_about_partiality(self):
        out = format_fetch_filing(self._result(form_type="10-K"))
        assert "PARTIAL" not in out
