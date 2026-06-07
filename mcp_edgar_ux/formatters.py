"""
BBG Lite formatters for MCP tool results

Format handler results as Bloomberg Terminal-inspired text output.
Used by both CLI and MCP adapters for consistent presentation.
"""

from typing import Any, Optional


def _fmt_accepted(iso: Optional[str]) -> str:
    """'2026-06-05T20:05:28-04:00' -> '06-05 20:05' (ET, compact)"""
    if not iso or len(iso) < 16:
        return ""
    return f"{iso[5:10]} {iso[11:16]}"


def _fmt_num(value: Optional[float]) -> str:
    """Format share/dollar numbers with thousands separators"""
    if value is None:
        return "-"
    if value == int(value):
        return f"{int(value):,}"
    return f"{value:,.2f}"


def format_fetch_filing(result: dict[str, Any]) -> str:
    """Format fetch_filing result as BBG Lite text.

    Example output:
        TSLA 10-K | 2025-04-30 | FETCHED (cached)

        COMPANY:     Tesla, Inc.
        FORM:        10-K
        FILED:       2025-04-30
        SIZE:        427 KB (10,234 lines)

        PATH: /var/idio-mcp-cache/sec-filings/TSLA/10-K/2025-04-30.txt

        Try: Read(path, offset=0, limit=50) | search_filing("TSLA", "10-K", "SEARCH TERM")
    """
    if not result.get("success"):
        return f"ERROR: {result.get('error', 'Unknown error')}"

    meta = result['metadata']
    lines = []

    # Header
    cached_indicator = "(cached)" if result.get('cached') else "(downloaded)"
    lines.append(f"{meta['ticker'].upper()} {meta['form_type'].upper()} | {meta['filing_date']} | FETCHED {cached_indicator}")
    lines.append("")

    # Metadata
    company = meta.get('company', 'N/A')
    lines.append(f"COMPANY:     {company}")
    lines.append(f"FORM:        {meta['form_type']}")
    lines.append(f"FILED:       {meta['filing_date']}")

    size_kb = meta['size_bytes'] / 1024
    size_str = f"{size_kb:.0f} KB"
    if meta.get('total_lines'):
        size_str += f" ({meta['total_lines']:,} lines)"
    lines.append(f"SIZE:        {size_str}")
    lines.append("")

    # Path
    lines.append(f"PATH: {result['path']}")

    # Affordances
    lines.append("")
    lines.append(f'Try: Read(path, offset=0, limit=50) | search_filing("{meta["ticker"]}", "{meta["form_type"]}", "SEARCH TERM")')

    return "\n".join(lines)


def format_search_filing(result: dict[str, Any]) -> str:
    """Format search_filing result as BBG Lite text.

    Example output:
        TSLA 10-K | 2025-04-30 | SEARCH "supply chain"

        MATCHES (12 found | 10,234 lines)
        ──────────────────────────────────────────────────────────────────────
          1234: matching line with supply chain
          1235: context after

          2456: another matching line
          2457: more context

        PATH: /var/idio-mcp-cache/sec-filings/TSLA/10-K/2025-04-30.txt
        Try: Read(path, offset=LINE, limit=50) | search_filing(..., pattern="OTHER")
    """
    if not result.get("success"):
        return f"ERROR: {result.get('error', 'Unknown error')}"

    meta = result['metadata']
    pattern = result['pattern']
    match_count = result['match_count']
    file_path = result['file_path']
    offset = result.get('offset', 0)

    # No matches
    if match_count == 0:
        return f"""{meta['ticker'].upper()} {meta['form_type'].upper()} | {meta['filing_date']} | SEARCH "{pattern}"

NO MATCHES FOUND

PATH: {file_path}
Try: Different search term | Read(path) for full filing
"""

    lines = []

    # Header with full file path
    lines.append(f"{meta['ticker'].upper()} {meta['form_type'].upper()} | {meta['filing_date']} | SEARCH \"{pattern}\"")
    lines.append(f"FILE: {file_path}")
    lines.append("")

    # Summary with correct range
    returned = len(result['matches'])
    start_idx = offset + 1
    end_idx = offset + returned

    if match_count > returned:
        if offset == 0:
            range_str = f" (showing first {returned})"
        else:
            range_str = f" (showing {start_idx}-{end_idx})"
    else:
        range_str = ""
    lines.append(f"MATCHES ({match_count} found{range_str})")
    lines.append("─" * 70)

    # Format each match with line numbers
    for i, match in enumerate(result['matches'], 1):
        if i > 1:
            lines.append("")  # Blank line between matches

        line_num = match['line_number']
        context_before = match.get('context_before', [])
        context_after = match.get('context_after', [])

        # Context before (with calculated line numbers)
        for j, ctx_line in enumerate(context_before):
            ctx_line_num = line_num - len(context_before) + j
            lines.append(f"  {ctx_line_num:>4}: {ctx_line}")

        # Matching line
        lines.append(f"  {line_num:>4}: {match['line']}")

        # Context after (with calculated line numbers)
        for j, ctx_line in enumerate(context_after, 1):
            ctx_line_num = line_num + j
            lines.append(f"  {ctx_line_num:>4}: {ctx_line}")

    lines.append("")
    lines.append(f"PATH: {file_path}")

    # Navigation hints
    if match_count > returned:
        lines.append(f'More: search_filing(..., max_results={match_count}) | Read(path, offset=LINE, limit=50)')
    else:
        lines.append(f'Try: Read(path, offset=LINE, limit=50) | search_filing(..., pattern="OTHER")')

    return "\n".join(lines)


def format_list_filings(result: dict[str, Any]) -> str:
    """Format list_filings result as BBG Lite text.

    Example output (single ticker):
        TSLA 10-K FILINGS AVAILABLE
        ────────────────────────────────────────────────────────────────
        83 filings available (2 cached)

        Date         Location (if cached)
        ────────────────────────────────────────────────────────────────
        2025-11-19   (not cached - will download on demand)
        2025-08-27   /var/idio-mcp-cache/sec-filings/TSLA/10-K/2025-08-27.txt
        ...

    Example output (multiple tickers):
        10-K FILINGS AVAILABLE (LATEST ACROSS ALL COMPANIES)
        ────────────────────────────────────────────────────────────────
        100 filings available (5 cached)

        TICKER      COMPANY                         FILED       PATH (if cached)
        ────────────────────────────────────────────────────────────────────────────────────────────────────────────
        AAPL        Apple Inc.                      2025-11-19  /var/idio-mcp-cache/sec-filings/AAPL/10-K/2025-11-19.txt
        TSLA        Tesla, Inc.                     2025-11-18  [Fetch]
        ...
    """
    if not result.get("success"):
        return f"ERROR: {result.get('error', 'Unknown error')}"

    lines = []

    # Get pagination parameters
    start = result.get('start', 0)
    max_results = result.get('max', 15)
    total_count = result['count']

    # Calculate pagination
    end = min(start + max_results, total_count)
    filings_to_show = result['filings'][start:end]

    # Check if we have multiple tickers (if so, show ticker column)
    unique_tickers = set(f['ticker'].upper() for f in filings_to_show)
    multi_ticker = len(unique_tickers) > 1

    # Check if we should show form column
    # Always show for CORE/ALL (mixed form types), or if current page has multiple types
    requested_form = result.get('requested_form_type', '').upper()
    unique_forms = set(f['form_type'].upper() for f in filings_to_show)
    multi_form = requested_form in ('CORE', 'ALL') or len(unique_forms) > 1

    if multi_ticker:
        # Multiple tickers - show ticker column with company name
        # Use requested form_type (e.g., "CORE") instead of first filing's type
        form_type = result.get('requested_form_type', result['filings'][0]['form_type'] if result['filings'] else "ALL").upper()

        since_note = f" — ACCEPTED SINCE {result['since']}" if result.get('since') else ""
        if multi_form:
            # Show FORM column when displaying multiple form types
            lines.append(f"{form_type} FILINGS AVAILABLE (RECENT FILINGS - PAST FEW DAYS){since_note}")
            lines.append("─" * 120)
            lines.append(f"{'TICKER':<10}  {'FORM':<12}  {'COMPANY':<30}  {'FILED':<10}  {'ACCEPTED(ET)':<12}  PATH (if cached)")
            lines.append("─" * 120)
        else:
            lines.append(f"{form_type} FILINGS AVAILABLE (RECENT FILINGS - PAST FEW DAYS){since_note}")
            lines.append("─" * 100)
            lines.append(f"{'TICKER':<10}  {'COMPANY':<30}  {'FILED':<10}  {'ACCEPTED(ET)':<12}  PATH (if cached)")
            lines.append("─" * 100)

        # Table rows (paginated)
        for filing in filings_to_show:
            ticker = filing['ticker'][:10].ljust(10)
            form = filing['form_type'][:12].ljust(12) if multi_form else None
            company_name = filing.get('company_name', 'N/A')
            # Truncate company name to 30 chars and pad for alignment
            company = ((company_name[:27] + '...') if company_name and len(company_name) > 30 else (company_name or 'N/A')).ljust(30)
            date = filing['filing_date'][:10].ljust(10)
            cached_info = filing.get('cached', {})

            # Get cached path if available (same logic as single-ticker)
            path = None
            if cached_info and isinstance(cached_info, dict):
                # Get first available format path (prefer txt, then md, then any)
                for fmt in ['txt', 'md']:
                    if fmt in cached_info:
                        fmt_data = cached_info[fmt]
                        if isinstance(fmt_data, dict) and 'path' in fmt_data:
                            path = fmt_data['path']
                            break

                # If no txt/md, get first available format
                if not path:
                    for fmt_data in cached_info.values():
                        if isinstance(fmt_data, dict) and 'path' in fmt_data:
                            path = fmt_data['path']
                            break

            # Show path if cached, blank otherwise
            location = path if path else ""
            accepted = _fmt_accepted(filing.get('acceptance_datetime')).ljust(12)

            if multi_form:
                lines.append(f"{ticker}  {form}  {company}  {date}  {accepted}  {location}")
            else:
                lines.append(f"{ticker}  {company}  {date}  {accepted}  {location}")
    else:
        # Single ticker - original format
        ticker = result['filings'][0]['ticker'].upper() if result['filings'] else "FILINGS"
        # Use requested form_type (e.g., "CORE") instead of first filing's type
        requested_form_type = result.get('requested_form_type', result['filings'][0]['form_type'] if result['filings'] else "").upper()
        company_name = result['filings'][0].get('company_name', '') if result['filings'] else ""

        # Include company name in header if available
        if company_name:
            lines.append(f"{ticker} ({company_name}) {requested_form_type} FILINGS AVAILABLE")
        else:
            lines.append(f"{ticker} {requested_form_type} FILINGS AVAILABLE")
        lines.append("─" * 70)

        if result.get('since'):
            lines.append(f"ACCEPTED SINCE {result['since']}")

        # Show form type column for CORE/ALL (multiple form types)
        if multi_form:
            lines.append(f"{'FORM':<12}  {'FILED':<10}  {'ACCEPTED(ET)':<12}  LOCATION (if cached)")
        else:
            lines.append(f"{'FILED':<10}  {'ACCEPTED(ET)':<12}  LOCATION (if cached)")
        lines.append("─" * 70)

        # Table rows (paginated)
        for filing in filings_to_show:
            form_col = filing['form_type'][:12].ljust(12) if multi_form else None
            date = filing['filing_date'][:10].ljust(10)
            cached_info = filing.get('cached', {})

            # Get cached path if available
            path = None
            if cached_info and isinstance(cached_info, dict):
                # Get first available format path (prefer txt, then md, then any)
                for fmt in ['txt', 'md']:
                    if fmt in cached_info:
                        fmt_data = cached_info[fmt]
                        if isinstance(fmt_data, dict) and 'path' in fmt_data:
                            path = fmt_data['path']
                            break

                # If no txt/md, get first available format
                if not path:
                    for fmt_data in cached_info.values():
                        if isinstance(fmt_data, dict) and 'path' in fmt_data:
                            path = fmt_data['path']
                            break

            location = path if path else ""
            accepted = _fmt_accepted(filing.get('acceptance_datetime')).ljust(12)

            if multi_form:
                lines.append(f"{form_col}  {date}  {accepted}  {location}")
            else:
                lines.append(f"{date}  {accepted}  {location}".rstrip())

    # Footer
    lines.append("")
    lines.append(f"Showing {start + 1}-{start + len(filings_to_show)} of {total_count} filings ({result['cached_count']} cached)")
    lines.append("")
    lines.append(f"Try: fetch_filing(ticker, form, date) | search_filing(ticker, form, pattern)")
    lines.append(f"     Read(path) to read cached filing directly")

    return "\n".join(lines)


def format_financial_statements(result: dict[str, Any]) -> str:
    """Format get_financial_statements result as BBG Lite text.

    Example output:
        TSLA (Tesla, Inc.) | FINANCIAL STATEMENTS

        ═══════════════════════════════════════════════════════════════════
        INCOME STATEMENT • FY 2021-2024
        ═══════════════════════════════════════════════════════════════════

                                       FY 2024      FY 2023      FY 2022      FY 2021
        ───────────────────────────────────────────────────────────────────────────
        Total Revenue               $97,690,000  $96,773,000  $81,462,000  $53,823,000
        Operating Income             $7,076,000   $8,891,000  $13,656,000   $6,523,000
        Net Income                   $7,091,000  $14,997,000  $12,556,000   $5,519,000

        [Full table from edgartools]

        Try: get_financial_statements(ticker, statement_type="income") for specific statements
    """
    from edgar.richtools import repr_rich

    if not result.get("success"):
        error_msg = result.get('error', 'Unknown error')
        ticker = result.get('ticker', 'UNKNOWN')

        # Check for common error patterns and format nicely
        if "No financial data available" in error_msg or "warrant" in error_msg.lower():
            lines = [
                f"{ticker} | FINANCIAL STATEMENTS",
                "",
                "═" * 70,
                "NO DATA AVAILABLE",
                "═" * 70,
                "",
                "This ticker does not have financial statement data.",
                "",
                "Common reasons:",
                "  • Warrants (W suffix) - e.g., DNMXW is a warrant for DNMX",
                "  • Units (U suffix) - bundled securities",
                "  • Rights (R suffix) - subscription rights",
                "  • Recently listed companies - data not yet available",
                "",
                "─" * 70,
                "Try: Use the underlying common stock ticker instead",
                f"     e.g., if {ticker} is a warrant, try {ticker.rstrip('WUR')}",
            ]
            return "\n".join(lines)

        return f"ERROR: {error_msg}"

    company_name = result['company_name']
    ticker = result['ticker']
    statements = result['statements']

    lines = []

    # Header
    lines.append(f"{ticker} ({company_name}) | FINANCIAL STATEMENTS")
    lines.append("")

    # Render each statement
    for stmt_type, stmt_obj in statements.items():
        if stmt_obj is None:
            continue

        # Section header
        stmt_title = {
            "income": "INCOME STATEMENT",
            "balance": "BALANCE SHEET",
            "cash_flow": "CASH FLOW STATEMENT"
        }.get(stmt_type, stmt_type.upper())

        periods_str = " • ".join(stmt_obj.periods) if hasattr(stmt_obj, 'periods') else ""

        lines.append("═" * 70)
        lines.append(f"{stmt_title} {periods_str}")
        lines.append("═" * 70)
        lines.append("")

        # Calculate width based on number of periods to prevent truncation
        # ~150 chars for ≤4 periods, ~250 chars for 10 periods
        num_periods = len(stmt_obj.data.columns) if hasattr(stmt_obj, 'data') else 4
        if num_periods <= 4:
            width = 150
        elif num_periods <= 7:
            width = 200
        else:
            width = 250

        # Use repr_rich with appropriate width instead of str() to prevent number truncation
        lines.append(repr_rich(stmt_obj.__rich__(), width=width))
        lines.append("")

    # Affordances
    lines.append("─" * 70)
    lines.append(f'Try: get_financial_statements("{ticker}", statement_type="income") for specific statements')

    return "\n".join(lines)


def format_insider_activity(result: dict[str, Any]) -> str:
    """Format insider_activity result as BBG Lite text, series-aggregated per filer.

    The signal is the SERIES — sustained distribution across multiple filings
    is grouped per filer so it can't be missed in single-filing reads.
    """
    if not result.get("success"):
        return f"ERROR: {result.get('error', 'Unknown error')}"

    ticker = result["ticker"]
    days = result["days"]
    filings = result["filings"]

    lines = []
    lines.append(f"{ticker} INSIDER ACTIVITY — LAST {days} DAYS (Forms 3/4/5/144)")
    lines.append("─" * 100)

    if not filings:
        lines.append("No ownership filings in window.")
        lines.append("")
        lines.append(f'Try: insider_activity("{ticker}", days=90) for a wider window')
        return "\n".join(lines)

    # Group by filer (preserve newest-first order within each group)
    by_filer: dict[str, list[dict]] = {}
    for f in filings:
        by_filer.setdefault(f["filer"], []).append(f)

    # Order filers by gross transaction value (largest series first)
    def gross_value(fs: list[dict]) -> float:
        return sum(t["value"] or 0 for f in fs for t in f["transactions"])

    for filer in sorted(by_filer, key=lambda k: gross_value(by_filer[k]), reverse=True):
        group = by_filer[filer]
        position = next((f["position"] for f in group if f.get("position")), None)

        # Series rollup across the filer's Form 4/5 transactions (144s are notices, not executions)
        sold_sh = sold_val = bought_sh = bought_val = 0.0
        n_txn_filings = 0
        proposed_sh = 0.0
        n_144 = 0
        plan_flags = set()
        for f in group:
            base_form = f["form_type"].replace("/A", "")
            if base_form == "144":
                n_144 += 1
                proposed_sh += sum(t["shares"] or 0 for t in f["transactions"])
                continue
            if f["transactions"]:
                n_txn_filings += 1
            if f["aff10b5One"] is not None:
                plan_flags.add(f["aff10b5One"])
            for t in f["transactions"]:
                if t["code"] == "S":
                    sold_sh += t["shares"] or 0
                    sold_val += t["value"] or 0
                elif t["code"] == "P":
                    bought_sh += t["shares"] or 0
                    bought_val += t["value"] or 0

        lines.append("")
        header = filer if not position else f"{filer} ({position})"
        lines.append(header.upper())

        series_bits = []
        if sold_sh:
            series_bits.append(f"SOLD {_fmt_num(sold_sh)} sh ≈ ${_fmt_num(sold_val)} across {n_txn_filings} filing(s)")
        if bought_sh:
            series_bits.append(f"BOUGHT {_fmt_num(bought_sh)} sh ≈ ${_fmt_num(bought_val)}")
        if n_144:
            series_bits.append(f"{n_144} Form 144 notice(s) for {_fmt_num(proposed_sh)} sh proposed")
        if plan_flags == {True}:
            plan_str = "10b5-1 PLAN (all filings)"
        elif plan_flags == {False}:
            plan_str = "DISCRETIONARY (no 10b5-1 plan)"
        elif plan_flags:
            plan_str = "MIXED 10b5-1 status"
        else:
            plan_str = None
        if plan_str:
            series_bits.append(plan_str)
        if series_bits:
            lines.append(f"  SERIES: {' | '.join(series_bits)}")

        for f in group:
            accepted = _fmt_accepted(f.get("acceptance_datetime"))
            flag = ""
            if f["aff10b5One"] is True:
                flag = "  [10b5-1]"
            elif f["aff10b5One"] is False:
                flag = "  [discretionary]"
            lines.append(f"  {f['form_type']:<5} filed {f['filing_date']}  acc {accepted}{flag}  {f['accession_number']}")
            for t in f["transactions"]:
                px = f" @ {_fmt_num(t['price'])}" if t["price"] is not None else ""
                val = f"  = ${_fmt_num(t['value'])}" if t["value"] else ""
                post = f"  post {_fmt_num(t['shares_owned_after'])}" if t["shares_owned_after"] is not None else ""
                lines.append(f"        {t['date']}  {t['code']:<4} {t['description'][:28]:<28} {_fmt_num(t['shares']):>12}{px}{val}{post}")
            for fn_id, fn_text in f["footnotes"].items():
                text = fn_text if len(fn_text) <= 110 else fn_text[:107] + "..."
                lines.append(f"        {fn_id}: {text}")
            if f.get("remarks"):
                lines.append(f"        Remarks: {f['remarks'][:110]}")

    lines.append("")
    lines.append(f"{len(filings)} filing(s), {len(by_filer)} filer(s)")
    lines.append("")
    lines.append(f'Try: fetch_filing("{ticker}", "4", format="xml") for raw XML | insider_activity("{ticker}", days=90)')

    return "\n".join(lines)


