"""
MCP Tool Handlers

Shared handlers for MCP tools that use the hexagonal core.
"""
import asyncio
from typing import Any, Optional

from ...container import Container


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
        force_refetch: bool = False
    ) -> dict[str, Any]:
        """Fetch filing and return path + preview + metadata"""
        try:
            # Get list of cached filings to check if this one exists
            cached_filings = await asyncio.to_thread(
                self.container.cache.list_all,
                ticker=ticker,
                form_type=form_type
            )
            # Normalize format names: cache uses extensions (txt, md, html), API uses full names
            format_map = {"text": "txt", "markdown": "md", "html": "html"}
            normalized_format = format_map.get(format, format)
            cached_dates = {(c.filing_date, c.format) for c in cached_filings}

            # Fetch the filing (may use cache or download)
            filing_content = await asyncio.to_thread(
                self.container.fetch_filing.execute,
                ticker=ticker,
                form_type=form_type,
                date=date,
                format=format,
                include_exhibits=True,
                preview_lines=preview_lines,
                force_refetch=force_refetch
            )

            # Check if this filing was already cached before we called the service
            was_cached = (filing_content.filing.filing_date, normalized_format) in cached_dates

            # No preview - agent should use Read tool on the returned path
            return {
                "success": True,
                "path": str(filing_content.path),
                "cached": was_cached,
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
        max: int = 15
    ) -> dict[str, Any]:
        """List available filings (both cached and from SEC)

        If ticker is None, returns latest filings across all companies.
        """
        try:
            # Use service from container
            available, cached = await asyncio.to_thread(
                self.container.list_filings.execute,
                ticker=ticker,
                form_type=form_type
            )

            # Build map of cached filings by (ticker, form_type, filing_date)
            cached_map = {}
            for c in cached:
                key = (c.ticker, c.form_type, c.filing_date)
                if key not in cached_map:
                    cached_map[key] = {}
                cached_map[key][c.format] = {
                    "path": str(c.path),
                    "size_bytes": c.size_bytes
                }

            # Merge available with cached info
            filings = []
            for filing in available:
                key = (filing.ticker, filing.form_type.upper(), filing.filing_date)
                cached_info = cached_map.get(key, {})
                filings.append({
                    "ticker": filing.ticker,
                    "form_type": filing.form_type,
                    "filing_date": filing.filing_date,
                    "company_name": filing.company_name,
                    "accession_number": filing.accession_number,
                    "sec_url": filing.sec_url,
                    "cached": cached_info
                })

            return {
                "success": True,
                "requested_form_type": form_type,  # Preserve requested form_type for header
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
