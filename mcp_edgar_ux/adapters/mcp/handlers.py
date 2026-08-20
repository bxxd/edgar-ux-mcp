"""
MCP Tool Handlers

Shared handlers for MCP tools that use the hexagonal core.
"""
import asyncio
from typing import Any, Optional

from ...container import Container
from ..edgar import normalize_identifier


class MCPHandlers:
    """Handlers for MCP tools using dependency injection"""

    def __init__(self, container: Container):
        self.container = container

    async def fetch_filing(
        self,
        ticker: str,
        form_type: str,
        date: Optional[str] = None,
        format: str = "text",
        preview_lines: int = 200,
        force_refetch: bool = False,
        document: Optional[str] = None
    ) -> dict[str, Any]:
        """Fetch filing and return path + preview + metadata"""
        try:
            # Probe the cache under the label it is stored by. A CIK filer is
            # saved as CIK##########, so probing the caller's raw digits finds
            # nothing and every fetch reports itself freshly downloaded.
            cache_label, _ = normalize_identifier(ticker)
            cached_filings = await asyncio.to_thread(
                self.container.cache.list_all,
                ticker=cache_label,
                form_type=form_type
            )
            # Normalize format names: cache uses extensions (txt, md, html, xml), API uses full names
            format_map = {"text": "txt", "markdown": "md", "html": "html", "xml": "xml"}
            normalized_format = format_map.get(format, format)
            cached_accessions = {(c.accession_number, c.format) for c in cached_filings}

            # Fetch the filing (may use cache or download)
            filing_content = await asyncio.to_thread(
                self.container.fetch_filing.execute,
                ticker=ticker,
                form_type=form_type,
                date=date,
                format=format,
                include_exhibits=True,
                preview_lines=preview_lines,
                force_refetch=force_refetch,
                document=document
            )

            # Check if this filing was already cached before we called the service
            was_cached = (
                False if document
                else (filing_content.filing.accession_number, normalized_format)
                in cached_accessions
            )

            # No preview - agent should use Read tool on the returned path
            return {
                "success": True,
                "path": str(filing_content.path),
                "cached": was_cached,
                "document": filing_content.document,
                "partial": bool(filing_content.omitted_documents),
                "omitted_documents": [
                    {
                        "sequence": d.sequence,
                        "document_type": d.document_type,
                        "document": d.document,
                        "description": d.description,
                    }
                    for d in filing_content.omitted_documents
                ],
                "metadata": {
                    "company": filing_content.filing.company_name,
                    "ticker": filing_content.filing.ticker,
                    "form_type": filing_content.filing.form_type,
                    "filing_date": filing_content.filing.filing_date,
                    "accession_number": filing_content.filing.accession_number,
                    "sec_url": filing_content.filing.sec_url,
                    "format": filing_content.format,
                    "size_bytes": filing_content.size_bytes,
                    "total_lines": filing_content.total_lines,
                }
            }

        except Exception as e:
            return {
                "success": False,
                "error": f"Failed to fetch filing: {str(e)}"
            }

    async def list_documents(
        self,
        ticker: str,
        form_type: str,
        date: Optional[str] = None
    ) -> dict[str, Any]:
        """List every document inside a filing's accession"""
        try:
            filing, documents = await asyncio.to_thread(
                self.container.list_documents.execute,
                ticker=ticker,
                form_type=form_type,
                date=date
            )

            return {
                "success": True,
                "metadata": {
                    "company": filing.company_name,
                    "ticker": filing.ticker,
                    "form_type": filing.form_type,
                    "filing_date": filing.filing_date,
                    "accession_number": filing.accession_number,
                    "sec_url": filing.sec_url,
                },
                "documents": [
                    {
                        "sequence": d.sequence,
                        "document_type": d.document_type,
                        "document": d.document,
                        "description": d.description,
                        "is_primary": d.is_primary,
                        "url": d.url,
                    }
                    for d in documents
                ],
                "count": len(documents),
            }

        except Exception as e:
            return {
                "success": False,
                "error": f"Failed to list documents: {str(e)}"
            }

    async def thirteenf_holdings(
        self,
        ticker: str,
        date: Optional[str] = None,
        form_type: str = "13F-HR",
        max_holdings: int = 50
    ) -> dict[str, Any]:
        """13F holdings from the information table"""
        try:
            report = await asyncio.to_thread(
                self.container.thirteenf.execute,
                ticker=ticker,
                date=date,
                form_type=form_type
            )

            return {
                "success": True,
                "manager": report.manager,
                "report_period": report.report_period,
                "cover_total_holdings": report.cover_total_holdings,
                "cover_total_value": report.cover_total_value,
                "table_total_value": report.table_total_value,
                "reconciles": report.reconciles,
                "reported_in_thousands": report.reported_in_thousands,
                "max_holdings": max_holdings,
                "metadata": {
                    "ticker": report.filing.ticker,
                    "company": report.filing.company_name,
                    "form_type": report.filing.form_type,
                    "filing_date": report.filing.filing_date,
                    "accession_number": report.filing.accession_number,
                    "sec_url": report.filing.sec_url,
                },
                "holdings": [
                    {
                        "issuer": h.issuer,
                        "title_of_class": h.title_of_class,
                        "cusip": h.cusip,
                        "ticker": h.ticker,
                        "value": h.value,
                        "shares": h.shares,
                        "share_type": h.share_type,
                        "put_call": h.put_call,
                        "investment_discretion": h.investment_discretion,
                    }
                    for h in report.holdings
                ],
                "count": len(report.holdings),
            }

        except Exception as e:
            return {
                "success": False,
                "error": f"Failed to get 13F holdings for {ticker}: {str(e)}"
            }

    async def search_filing(
        self,
        ticker: str,
        form_type: str,
        pattern: str,
        date: Optional[str] = None,
        format: str = "text",
        context_lines: int = 2,
        max_results: int = 20,
        offset: int = 0
    ) -> dict[str, Any]:
        """Search for pattern in filing"""
        try:
            # Use service from container
            result = await asyncio.to_thread(
                self.container.search_filing.execute,
                ticker=ticker,
                form_type=form_type,
                pattern=pattern,
                date=date,
                format=format,
                context_lines=context_lines,
                max_results=max_results,
                offset=offset
            )

            # Format matches for output
            formatted_matches = []
            for match in result.matches:
                formatted_matches.append({
                    "line_number": match.line_number,
                    "line": match.line_content,
                    "context_before": match.context_before,
                    "context_after": match.context_after
                })

            return {
                "success": True,
                "pattern": result.pattern,
                "matches": formatted_matches,
                "match_count": result.total_matches,
                "offset": offset,
                "max_results": max_results,
                "file_path": str(result.file_path),
                "metadata": {
                    "ticker": result.filing.ticker,
                    "form_type": result.filing.form_type,
                    "filing_date": result.filing.filing_date,
                }
            }

        except Exception as e:
            return {
                "success": False,
                "error": f"Failed to search filing: {str(e)}"
            }

    async def list_filings(
        self,
        ticker: Optional[str],
        form_type: str,
        start: int = 0,
        max: int = 15,
        since: Optional[str] = None
    ) -> dict[str, Any]:
        """List available filings (both cached and from SEC)

        If ticker is None, returns latest filings across all companies.
        If since is set (ISO timestamp), only filings accepted at/after it.
        """
        try:
            # Use service from container
            available, cached = await asyncio.to_thread(
                self.container.list_filings.execute,
                ticker=ticker,
                form_type=form_type,
                since=since
            )

            # Keyed on the accession number: a date is shared by every filing
            # a company made that day, so keying on it hands each of them the
            # same path and reports all of them cached.
            cached_map = {}
            for c in cached:
                key = (c.ticker, c.form_type, c.accession_number)
                if key not in cached_map:
                    cached_map[key] = {}
                cached_map[key][c.format] = {
                    "path": str(c.path),
                    "size_bytes": c.size_bytes
                }

            # Merge available with cached info
            filings = []
            for filing in available:
                key = (filing.ticker, filing.form_type.upper(), filing.accession_number)
                cached_info = cached_map.get(key, {})
                filings.append({
                    "ticker": filing.ticker,
                    "form_type": filing.form_type,
                    "filing_date": filing.filing_date,
                    "acceptance_datetime": filing.acceptance_datetime,
                    "company_name": filing.company_name,
                    "accession_number": filing.accession_number,
                    "sec_url": filing.sec_url,
                    "cached": cached_info
                })

            return {
                "success": True,
                "requested_form_type": form_type,  # Preserve requested form_type for header
                "since": since,
                "filings": filings,
                "count": len(filings),
                "cached_count": len(cached),
                "available_count": len(available),
                "start": start,
                "max": max
            }

        except Exception as e:
            return {
                "success": False,
                "error": f"Failed to list filings: {str(e)}"
            }

    async def insider_activity(
        self,
        ticker: str,
        days: int = 30
    ) -> dict[str, Any]:
        """Insider activity (Forms 3/4/5/144) over the last N days"""
        try:
            filings = await asyncio.to_thread(
                self.container.insider_activity.execute,
                ticker=ticker,
                days=days
            )

            return {
                "success": True,
                "ticker": ticker.upper(),
                "days": days,
                "filings": [
                    {
                        "filer": f.filer,
                        "position": f.position,
                        "form_type": f.form_type,
                        "filing_date": f.filing_date,
                        "acceptance_datetime": f.acceptance_datetime,
                        "accession_number": f.accession_number,
                        "sec_url": f.sec_url,
                        "aff10b5One": f.aff10b5One,
                        "transactions": [
                            {
                                "date": t.date,
                                "code": t.code,
                                "description": t.description,
                                "shares": t.shares,
                                "price": t.price,
                                "value": t.value,
                                "shares_owned_after": t.shares_owned_after,
                            }
                            for t in f.transactions
                        ],
                        "footnotes": f.footnotes,
                        "remarks": f.remarks,
                    }
                    for f in filings
                ],
                "count": len(filings),
            }

        except Exception as e:
            return {
                "success": False,
                "ticker": ticker.upper(),
                "error": f"Failed to get insider activity for {ticker}: {str(e)}"
            }

    async def get_financial_statements(
        self,
        ticker: str,
        statement_type: str = "all"
    ) -> dict[str, Any]:
        """Get structured financial statements from Entity Facts API"""
        try:
            # Call service (wrapped in to_thread for async compatibility)
            result = await asyncio.to_thread(
                self.container.get_financials.execute,
                ticker=ticker,
                statement_type=statement_type
            )

            # Add success flag and return
            return {
                "success": True,
                **result
            }

        except Exception as e:
            return {
                "success": False,
                "ticker": ticker.upper(),
                "error": f"Failed to get financial statements for {ticker}: {str(e)}"
            }
