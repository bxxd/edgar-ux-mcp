"""
MCP Tool Definitions

Single source of truth for tool schemas and descriptions.
Used by both stdio and HTTP/SSE servers.
"""

# Tool schemas for MCP
TOOL_SCHEMAS = {
    "fetch_filing": {
        "name": "fetch_filing",
        "description": """Download SEC filing to disk. Returns path for Read/Grep/search_filing.

fetch_filing("TSLA", "10-K") → {path: ".../TSLA/10-K/2024-01-29.txt", cached: true}
fetch_filing("MP", "4", format="xml") → raw XML (lossless: 10b5-1 checkbox, footnotes)

Use format="xml" for Forms 3/4/5/144 — the text render DROPS the aff10b5One
checkbox and footnotes. For aggregated insider reads, prefer insider_activity().
""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "Stock ticker (e.g. TSLA, AAPL)"
                },
                "form_type": {
                    "type": "string",
                    "description": "Form type (e.g. 10-K, 10-Q, 8-K, DEF 14A)"
                },
                "date": {
                    "type": "string",
                    "description": "Date filter (YYYY-MM-DD). Returns filing >= date, or most recent if omitted."
                },
                "format": {
                    "type": "string",
                    "enum": ["text", "markdown", "html", "xml"],
                    "description": "Output format (text=clean, markdown=may have XBRL, html=raw, xml=lossless passthrough for Forms 3/4/5/144)"
                },
                "preview_lines": {
                    "type": "integer",
                    "description": "Deprecated - preview removed",
                    "default": 0
                },
                "force_refetch": {
                    "type": "boolean",
                    "description": "Re-download even if cached (use if cached version seems incorrect)",
                    "default": False
                }
            },
            "required": ["ticker", "form_type"]
        }
    },
    "search_filing": {
        "name": "search_filing",
        "description": """Search SEC filing for pattern. Auto-fetches if not cached. Fuzzy matching (1-char tolerance).

search_filing("TSLA", "10-K", "supply chain") → matches with line numbers + context
search_filing("LNG", "10-Q", "Corpus|Stage 3") → OR patterns with |
""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "Stock ticker (e.g. TSLA, AAPL)"
                },
                "form_type": {
                    "type": "string",
                    "description": "Form type (e.g. 10-K, 10-Q, 8-K)"
                },
                "pattern": {
                    "type": "string",
                    "description": "Search pattern (extended regex, case-insensitive, fuzzy=1)"
                },
                "date": {
                    "type": "string",
                    "description": "Date filter (YYYY-MM-DD). Returns filing >= date, or most recent if omitted."
                },
                "context_lines": {
                    "type": "integer",
                    "description": "Lines of context before/after each match",
                    "default": 2
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum matches to return",
                    "default": 20
                },
                "offset": {
                    "type": "integer",
                    "description": "Skip first N matches (pagination)",
                    "default": 0
                }
            },
            "required": ["ticker", "form_type", "pattern"]
        }
    },
    "list_filings": {
        "name": "list_filings",
        "description": """List available SEC filings with cached status. Newest first.

RECOMMENDED: Use 'CORE' for essential filings — 10-K, 10-Q, 20-F, 6-K, 8-K,
S-1/S-3/S-3ASR/S-4 (and their /A amendments).

list_filings("TSLA", "CORE") → TSLA's essential filings (recommended)
list_filings("TSLA", "10-K") → TSLA's 10-Ks only
list_filings("TSLA", "ALL") → all of TSLA's filings
list_filings(form_type="CORE") → latest CORE filings across all companies
list_filings("TSLA", "10-K", start=15) → pagination
list_filings(form_type="CORE", since="2026-06-05T16:15:00") → accepted at/after timestamp (ET)

Each filing shows acceptance datetime (ET) — use 'since' to diff what landed
after your last sweep.

CORE EXCLUDES, by design — do not use CORE to conclude something was not filed:
  - ownership forms (3/4/5/144) → use insider_activity(ticker)
  - 13D/13G ownership stakes    → form_type="ALL" or the specific form
  - proxy material (DEF 14A / DEFA14A / DFAN14A / PRE 14A) → form_type="ALL".
    For any holding with a live proxy contest, activist stake, merger vote or
    annual meeting, sweep that ticker with "ALL" — a contested vote is where
    the highest-signal filing of the week lives, and CORE will not show it.
""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "Stock ticker (e.g. TSLA, AAPL). Omit to see latest across all companies."
                },
                "form_type": {
                    "type": "string",
                    "description": "Form type: 'CORE' (recommended - 10-K, 10-Q, 20-F, 6-K, 8-K, S-1/S-3/S-3ASR/S-4; excludes ownership, 13D/G and proxy forms), 'ALL', or specific (10-K, 10-Q, DEF 14A, etc.)"
                },
                "start": {
                    "type": "integer",
                    "description": "Starting index (newest first)",
                    "default": 0
                },
                "max": {
                    "type": "integer",
                    "description": "Maximum filings to return",
                    "default": 15
                },
                "since": {
                    "type": "string",
                    "description": "ISO timestamp filter on ACCEPTANCE time (naive = US/Eastern). Only filings accepted at/after this moment. E.g. '2026-06-05T16:15:00'"
                }
            },
            "required": ["form_type"]
        }
    },
    "insider_activity": {
        "name": "insider_activity",
        "description": """Insider activity (Forms 4/5/144 + Form 3) for a ticker, series-aggregated per filer.

insider_activity("MP") → last 30 days: filer, code, shares, price, post-holdings,
aff10b5One (10b5-1 plan checkbox), footnotes — grouped per filer so sustained
distribution is visible (single-filing reads miss the series).

insider_activity("MP", days=90) → wider window

aff10b5One=False on sales = DISCRETIONARY (not under a trading plan) — decisive
for insider-signal reads. Form 144 rows are PROPOSED sales (notice only).
""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "Stock ticker (e.g. MP, TSLA)"
                },
                "days": {
                    "type": "integer",
                    "description": "Lookback window in days (1-365)",
                    "default": 30
                }
            },
            "required": ["ticker"]
        }
    },
    "get_financial_statements": {
        "name": "get_financial_statements",
        "description": """Get simplified GAAP financials (last 4 years). Quick trend checks only.

get_financial_statements("TSLA") → income, balance, cash_flow summary
get_financial_statements("TSLA", "income") → income statement only

For detailed analysis: use fetch_filing() for complete 10-K/10-Q.
""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "Stock ticker (e.g. TSLA, AAPL)"
                },
                "statement_type": {
                    "type": "string",
                    "enum": ["all", "income", "balance", "cash_flow"],
                    "description": "Which statements to return",
                    "default": "all"
                }
            },
            "required": ["ticker"]
        }
    }
}
