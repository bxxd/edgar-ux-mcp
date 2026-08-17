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
fetch_filing("1082621", "13F-HR", document="2") → a named doc from the accession

Returns the submission's PRIMARY document. A submission is a bundle, and for
some forms the primary document is only a cover page — the output flags this as
PARTIAL and names what it left behind. Reach those with document=<seq|filename>.

13F-HR: the primary doc is the cover page (a total with NO issuer names). Use
thirteenf_holdings() for the information table.

Use format="xml" for Forms 3/4/5/144 — the text render DROPS the aff10b5One
checkbox and footnotes. For aggregated insider reads, prefer insider_activity().
""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "Ticker (e.g. TSLA) or CIK (e.g. 1082621, CIK0001082621). Use a CIK for filers with no ticker — 13F managers, endowments, family offices."
                },
                "document": {
                    "type": "string",
                    "description": "Fetch this document from the accession instead of the primary one: sequence number ('2') or filename ('information_table.xml'). See list_documents()."
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
                    "description": "Ticker (e.g. TSLA) or CIK (e.g. 1082621) for filers with no ticker"
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

RECOMMENDED: Use 'CORE' for essential filings (10-K, 10-Q, 20-F, 8-K, S-1/S-3/S-4, 13D/13G).

list_filings("TSLA", "CORE") → TSLA's essential filings (recommended)
list_filings("TSLA", "10-K") → TSLA's 10-Ks only
list_filings("TSLA", "ALL") → all of TSLA's filings
list_filings(form_type="CORE") → latest CORE filings across all companies
list_filings("TSLA", "10-K", start=15) → pagination
list_filings(form_type="CORE", since="2026-06-05T16:15:00") → accepted at/after timestamp (ET)
list_filings("1082621", "13F-HR") → a 13F filer addressed by CIK (no ticker exists)

Each filing shows acceptance datetime (ET) — use 'since' to diff what landed
after your last sweep. NOTE: CORE excludes ownership forms (3/4/5/144) — use
insider_activity(ticker) for those.
""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "Ticker (e.g. TSLA) or CIK (e.g. 1082621, CIK0001082621). Omit to see latest across all companies. 13F filers, endowments and family offices are holders, not issuers — they have a CIK and no ticker."
                },
                "form_type": {
                    "type": "string",
                    "description": "Form type: 'CORE' (recommended - 10-K, 10-Q, 20-F, 8-K, S-*, 13D/G), 'ALL', or specific (10-K, 10-Q, etc.)"
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
    "list_documents": {
        "name": "list_documents",
        "description": """List every document inside a filing's accession.

list_documents("NVDA", "13F-HR") → 1 primary_doc.xml (cover page)
                                   2 INFORMATION TABLE 56904.xml  ← the holdings

fetch_filing() returns only the PRIMARY document. This shows what else is in
the submission, so a cover page is never mistaken for the whole filing. Fetch
any of them with fetch_filing(..., document="<sequence or filename>").
""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "Ticker (e.g. TSLA) or CIK (e.g. 1082621) for filers with no ticker"
                },
                "form_type": {
                    "type": "string",
                    "description": "Form type (e.g. 13F-HR, 10-K, 8-K)"
                },
                "date": {
                    "type": "string",
                    "description": "Date filter (YYYY-MM-DD). Returns filing >= date, or most recent if omitted."
                }
            },
            "required": ["ticker", "form_type"]
        }
    },
    "thirteenf_holdings": {
        "name": "thirteenf_holdings",
        "description": """13F portfolio holdings — the information table, with issuer names.

thirteenf_holdings("1082621") → HARVARD MANAGEMENT CO INC, 19 positions, $4.26B
thirteenf_holdings("NVDA") → NVIDIA CORP, 8 positions, $63.4B
thirteenf_holdings("1067983", date="2026-01-01") → as of a period

13F is a HOLDER-side form: the filer is an institutional manager, addressed by
CIK when it has no ticker (endowments, family offices, hedge funds are not
issuers). Holdings live in the information table, NOT the primary document —
fetch_filing() on a 13F returns only the cover page's grand total.

Output reconciles the table against the cover page (tableEntryTotal /
tableValueTotal) and says so explicitly if they disagree.
""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "CIK of the 13F filer (e.g. 1082621) or ticker if the manager is also a listed issuer (e.g. NVDA)"
                },
                "date": {
                    "type": "string",
                    "description": "Date filter (YYYY-MM-DD). Returns filing >= date, or most recent if omitted."
                },
                "form_type": {
                    "type": "string",
                    "description": "13F-HR (default) or 13F-HR/A for amendments",
                    "default": "13F-HR"
                },
                "max_holdings": {
                    "type": "integer",
                    "description": "Maximum positions to display, largest first (all are counted in totals)",
                    "default": 50
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
