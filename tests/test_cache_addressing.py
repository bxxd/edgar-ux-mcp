"""
Tests for how a cached filing is addressed on disk.

These exist because of a regression that shipped green. PR #15 changed
list_available's dedupe key from filing_date to accession_number — correctly,
since companies routinely file more than once a day — but the cache path stayed
{TICKER}/{FORM}/{DATE}.{ext}. The date had been unique by construction; after
the dedupe change it no longer was, so two filings on one day resolved to one
file and a fetch returned the wrong body under the right accession number.

Nothing in the suite caught it because the dedupe lives in the adapter and the
key lives in the cache, and no test made them meet. These do: they drive the
real FetchFilingService against the real FilesystemCache with a fake SEC.

Pure unit tests — no network.
"""
from pathlib import Path

import pytest

from mcp_edgar_ux.adapters.filesystem import FilesystemCache
from mcp_edgar_ux.core.domain import Filing
from mcp_edgar_ux.core.services import FetchFilingService

# Two 8-Ks, same issuer, same calendar day — the shape the dedupe fix admitted.
EARLY = Filing(
    ticker="VKTX",
    form_type="8-K",
    filing_date="2026-02-11",
    accession_number="0001375365-26-000011",
    sec_url="https://sec.gov/early",
)
LATE = Filing(
    ticker="VKTX",
    form_type="8-K",
    filing_date="2026-02-11",
    accession_number="0001375365-26-000022",
    sec_url="https://sec.gov/late",
)

BODIES = {
    EARLY.accession_number: "BODY OF THE EARLY 8-K",
    LATE.accession_number: "BODY OF THE LATE 8-K",
}


class FakeSEC:
    """A SEC that holds both same-day filings and counts downloads."""

    def __init__(self, filings):
        self._by_accession = {f.accession_number: f for f in filings}
        self.downloads = 0

    def get_latest(self, ticker, form_type, date=None):
        raise NotImplementedError  # each test pins the filing it wants

    def fetch(self, filing, format="text", include_exhibits=True, document=None):
        self.downloads += 1
        return BODIES[filing.accession_number]

    def omitted_documents(self, filing, include_exhibits):
        return []


class CountingSearcher:
    def count_lines(self, path: Path) -> int:
        return path.read_text().count("\n") + 1


def _service(tmp_path, filings, resolve):
    sec = FakeSEC(filings)
    sec.get_latest = resolve
    return FetchFilingService(
        repository=FilesystemCache(tmp_path),
        fetcher=sec,
        searcher=CountingSearcher(),
    ), sec


class TestSameDayFilingsDoNotCollide:
    """The regression. Two filings, one date — two files, two bodies."""

    def test_second_filing_does_not_serve_the_first_ones_body(self, tmp_path):
        pending = [EARLY, LATE]
        svc, sec = _service(
            tmp_path, [EARLY, LATE], lambda t, f, d=None: pending.pop(0)
        )

        first = svc.execute("VKTX", "8-K")
        second = svc.execute("VKTX", "8-K", date="2026-02-11")

        assert first.filing.accession_number == EARLY.accession_number
        assert second.filing.accession_number == LATE.accession_number

        # The body must match the accession the tool reported, not the sibling's.
        assert first.path.read_text() == BODIES[EARLY.accession_number]
        assert second.path.read_text() == BODIES[LATE.accession_number]

    def test_both_filings_are_downloaded_not_one(self, tmp_path):
        pending = [EARLY, LATE]
        svc, sec = _service(
            tmp_path, [EARLY, LATE], lambda t, f, d=None: pending.pop(0)
        )

        svc.execute("VKTX", "8-K")
        svc.execute("VKTX", "8-K", date="2026-02-11")

        # A collision shows up here as a spurious cache hit: 1 download, not 2.
        assert sec.downloads == 2

    def test_each_filing_occupies_its_own_path(self, tmp_path):
        pending = [EARLY, LATE]
        svc, _ = _service(
            tmp_path, [EARLY, LATE], lambda t, f, d=None: pending.pop(0)
        )

        first = svc.execute("VKTX", "8-K")
        second = svc.execute("VKTX", "8-K", date="2026-02-11")

        assert first.path != second.path

    def test_refetching_the_same_filing_still_hits_the_cache(self, tmp_path):
        """Disambiguating must not defeat caching for the ordinary case."""
        svc, sec = _service(tmp_path, [EARLY], lambda t, f, d=None: EARLY)

        svc.execute("VKTX", "8-K")
        again = svc.execute("VKTX", "8-K")

        assert sec.downloads == 1
        assert again.path.read_text() == BODIES[EARLY.accession_number]


HARVARD = Filing(
    ticker="CIK0001082621",
    form_type="13F-HR",
    filing_date="2026-08-14",
    accession_number="0001082621-26-000004",
    sec_url="https://sec.gov/harvard",
)


class TestCIKAddressedFilerUsesTheCache:
    """A 13F filer has a CIK and no ticker.

    get_latest normalizes '1082621' to the label 'CIK0001082621' and save()
    writes under that label, so a read that probes the caller's raw string
    never finds anything and every call re-downloads.
    """

    def test_second_fetch_by_raw_cik_is_served_from_cache(self, tmp_path):
        BODIES[HARVARD.accession_number] = "HARVARD 13F COVER PAGE"
        svc, sec = _service(tmp_path, [HARVARD], lambda t, f, d=None: HARVARD)

        svc.execute("1082621", "13F-HR")
        svc.execute("1082621", "13F-HR")

        assert sec.downloads == 1

    def test_no_phantom_directory_under_the_raw_identifier(self, tmp_path):
        BODIES[HARVARD.accession_number] = "HARVARD 13F COVER PAGE"
        svc, _ = _service(tmp_path, [HARVARD], lambda t, f, d=None: HARVARD)

        svc.execute("1082621", "13F-HR")

        assert not (tmp_path / "1082621").exists()
        assert (tmp_path / "CIK0001082621").is_dir()


class TestListAllStillReportsFilingDates:
    """Whatever the filename becomes, list_all must still yield a real date."""

    def test_cached_filing_date_is_a_date_not_a_filename(self, tmp_path):
        pending = [EARLY, LATE]
        svc, _ = _service(
            tmp_path, [EARLY, LATE], lambda t, f, d=None: pending.pop(0)
        )
        svc.execute("VKTX", "8-K")
        svc.execute("VKTX", "8-K", date="2026-02-11")

        cached = FilesystemCache(tmp_path).list_all(ticker="VKTX", form_type="8-K")

        assert len(cached) == 2
        assert {c.filing_date for c in cached} == {"2026-02-11"}

    def test_disk_usage_counts_every_cached_file(self, tmp_path):
        pending = [EARLY, LATE]
        svc, _ = _service(
            tmp_path, [EARLY, LATE], lambda t, f, d=None: pending.pop(0)
        )
        svc.execute("VKTX", "8-K")
        svc.execute("VKTX", "8-K", date="2026-02-11")

        cache = FilesystemCache(tmp_path)
        expected = sum(c.size_bytes for c in cache.list_all())

        assert cache.get_disk_usage() == expected
        assert expected > 0


class TestCachedFlagIsHonest:
    """The tool must not report a cache hit as a fresh download.

    A CIK filer is stored under CIK##########; the handler probed the cache
    with the caller's raw digits, found nothing, and labelled every repeat
    fetch 'downloaded' while quietly serving it from disk.
    """

    @pytest.mark.asyncio
    async def test_repeat_fetch_of_a_cik_filer_reports_cached(self, tmp_path):
        from mcp_edgar_ux.adapters.mcp.handlers import MCPHandlers

        BODIES[HARVARD.accession_number] = "HARVARD 13F COVER PAGE"
        svc, sec = _service(tmp_path, [HARVARD], lambda t, f, d=None: HARVARD)

        class FakeContainer:
            pass

        container = FakeContainer()
        container.cache = svc.repository
        container.fetch_filing = svc
        handlers = MCPHandlers.__new__(MCPHandlers)
        handlers.container = container

        first = await handlers.fetch_filing(ticker="1082621", form_type="13F-HR")
        second = await handlers.fetch_filing(ticker="1082621", form_type="13F-HR")

        assert first["cached"] is False
        assert second["cached"] is True
        assert sec.downloads == 1
