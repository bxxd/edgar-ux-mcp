# mcp-edgar-ux MCP - Developer Context

MCP server for SEC EDGAR filings with Bloomberg Terminal-inspired formatted output.

**Public Repository**

---

## Overview

**SEC filing research tool for investment analysis.**

MCP (Model Context Protocol) server that provides programmatic access to SEC EDGAR filings with human-readable formatted output.

**Design Philosophy:**
- **Design for humans, AI benefits automatically** - BBG Terminal-inspired text formatting
- **Progressive disclosure** - Summary first, depth via separate calls
- **The Bitter Lesson** - Don't dump 241K tokens into context, save to disk and read what you need

**Workflow Pattern** (mirrors `Glob → Grep → Read`):
```python
# 1. Discover (like Glob)
list_filings("TSLA", "10-K")
→ Shows 20 years of 10-Ks, indicates which are cached

# 2. Download + Preview (like Download + head)
fetch_filing("TSLA", "10-K", "2024-01-29")
→ Downloads filing, shows first 50 lines, returns path

# 3. Search (like Grep)
search_filing("TSLA", "10-K", "supply chain")
→ Shows all "supply chain" mentions with line numbers and context

# 4. Deep Dive (use Read/Grep/Bash tools on cached path)
Read(path, offset=648, limit=50)
Grep("rare earth", path=cached_path)
```

---

## Architecture

### Stack

```
Python 3.11+
├── edgartools     → SEC EDGAR API (mature, handles SEC quirks)
├── mcp            → Model Context Protocol (stdio + SSE transports)
├── starlette      → Async web framework (SSE server)
├── markdownify    → HTML → Markdown conversion
└── BeautifulSoup  → XBRL/XML tag stripping
```

### Project Structure (Hexagonal Architecture)

```
mcp-edgar-ux/
├── mcp_edgar_ux/
│   ├── core/                    # BUSINESS LOGIC (domain + ports + services)
│   │   ├── domain.py            # Domain models (Filing, SearchResult, etc.)
│   │   ├── ports.py             # Port interfaces (Repository, Fetcher, Searcher)
│   │   └── services.py          # Use cases (FetchFilingService, etc.)
│   │
│   ├── adapters/                # INFRASTRUCTURE (implementations)
│   │   ├── filesystem.py        # Filesystem cache (implements Repository)
│   │   ├── edgar.py             # EDGAR fetcher (implements Fetcher)
│   │   ├── search.py            # Grep searcher (implements Searcher)
│   │   └── mcp/                 # MCP adapters
│   │       ├── tool_definitions.py  # Shared tool schemas (DRY)
│   │       └── handlers.py      # Shared MCP handlers
│   │
│   ├── container.py             # Dependency injection container
│   ├── server_http.py           # MCP HTTP/SSE server (170 lines, -81%)
│   └── cli.py                   # CLI for testing (uses container)
│
├── cli                          # Wrapper script
├── dev.py                       # Dev mode with auto-restart
├── README.md                    # User documentation
└── DEVELOPER.md                 # This file
```

### Async Architecture

**Pattern**: Sync blocking operations run in thread pool

```python
# Network I/O (edgartools is sync)
result = await asyncio.to_thread(fetcher.fetch_latest, ticker, form_type)

# Subprocess (grep is blocking)
result = await asyncio.to_thread(subprocess.run, grep_args, ...)

# File I/O (reading/writing cache)
path = await asyncio.to_thread(cache.save, ...)
```

**Why**: Keeps server responsive, allows concurrent MCP requests

### Addressing: ticker OR CIK

Every tool that takes `ticker` also takes a **CIK** (`1082621`, `0001082621`,
`CIK0001082621`). This is not a convenience — it is the only way to address a
whole class of filer.

EDGAR has issuer-side forms (10-K, 8-K: the filer is the company) and
**holder-side** forms (13F, 13D/G: the filer is whoever owns the position).
Holders are frequently not issuers: `HARVARD MANAGEMENT CO INC` is CIK
0001082621 with no ticker, because an endowment has no listed stock. A
ticker-keyed interface cannot reach it at all.

CIK-addressed filers get the cache label `CIK0001082621` so they can never be
confused with a ticker.

### Submissions are bundles, not files

An accession contains multiple documents. `fetch_filing()` returns the
**primary** one — and for some forms the primary document is not where the data
is. The canonical case is 13F-HR, whose primary document is a cover page
carrying `tableEntryTotal` and `tableValueTotal` and **not one issuer name**.

That is a dangerous shape: the cover page is a successful-looking fetch with an
authoritative-looking dollar total. So `fetch_filing()` never returns one
silently — it enumerates the accession and reports what it left behind:

```
PARTIAL — this is the primary document only.
1 document(s) in this accession were NOT returned:
  [ 2] INFORMATION TABLE    information_table.xml  INFORMATION TABLE

The primary document of a 13F is the COVER PAGE: it carries
tableEntryTotal and tableValueTotal and NOT ONE ISSUER NAME.
Do not report that total as the portfolio without the table behind it.
```

The "was anything left behind" rule is exact, not heuristic. A document counts
as omitted unless it is the primary, an `EX-*` exhibit (already appended), XBRL
viewer output (`IDEA: XBRL DOCUMENT`), or packaging (`GRAPHIC`/`CSS`/`JS`/
`JSON`/`ZIP`/`EXCEL`). Verified against real accessions: a 10-K's 12 extras and
an 8-K's 44 extras are all silent; a 13F's information table always fires.

### MCP Tools (7 tools)

**1. `list_filings(ticker, form_type, since=None)` - DISCOVERY**
- Shows available filings (both cached + available from SEC)
- `ticker` accepts a CIK — required for 13F filers, which have no ticker
- Every filing shows ACCEPTANCE datetime (ET) — the EDGAR-native time axis
- `since=<ISO timestamp>` filters on acceptance time (naive = US/Eastern) —
  diff "what landed after the last sweep ran" without hand-rolling the atom feed
- Returns: Table with cached indicator, dates, acceptance times

**2. `fetch_filing(ticker, form_type, date=None, format="text", document=None)` - DOWNLOAD**
- Downloads filing (if not cached), returns path
- Filing saved to disk (not loaded into context)
- Returns the submission's PRIMARY document, and **flags the fetch PARTIAL**
  when the accession holds substance it did not return (see above)
- `document=<sequence|filename>` fetches a specific document from the accession
  instead — the general answer to "give me the other documents in here".
  Cached at `{ID}/{FORM}/{DATE}-{ACCESSION}/{document}` so it is never mistaken
  for a filing
- `format="xml"` = lossless passthrough for Forms 3/4/5/144 (the text render
  DROPS the aff10b5One checkbox and all footnotes). It returns the primary XML
  document ALONE — no exhibits — so a Form 3 carrying an EX-24 power of
  attorney comes back flagged PARTIAL on this path and whole on the text path
- Returns: Path + metadata + `omitted_documents`

**2a. `list_documents(ticker, form_type, date=None)` - ACCESSION CONTENTS**
- Every document in the submission: sequence, type, filename, description
- Answers "what else is in this accession?" before committing to a fetch
- Feed a sequence or filename straight into `fetch_filing(..., document=...)`

**2b. `thirteenf_holdings(ticker, date=None, max_holdings=50)` - 13F POSITIONS**
- The information table: issuer, CUSIP, resolved ticker, value, % of book,
  shares — sorted largest first
- Address the manager by CIK (`thirteenf_holdings("1082621")`) or by ticker when
  the manager is itself listed (`thirteenf_holdings("NVDA")`)
- **Reconciles the table against the cover page** (`tableEntryTotal` /
  `tableValueTotal`) and says so explicitly when they disagree. Both totals are
  always printed together — a cover total with no table behind it is the exact
  bug this tool exists to prevent
- **Values are whole dollars.** Form 13F reported in THOUSANDS until the SEC
  amendment effective 2023-01-03 (Q4-2022 periods onward); earlier filings are
  rescaled and the output says so. Reconciliation cannot detect the unit,
  because the cover total carries the same one as the table it checks

**3. `search_filing(ticker, form_type, pattern, context_lines=2)` - CONTENT SEARCH**
- Fuzzy search using `ugrep` (fuzzy=1, tolerates 1-char differences)
- Pattern syntax: Extended regex (use `|` for OR, case-insensitive)
- Auto-downloads if not cached
- Returns: Matches with line numbers + context

**4. `get_financial_statements(ticker, statement_type="all")` - SIMPLIFIED FINANCIALS**
- Returns key GAAP metrics (Revenue, Net Income, Assets, Cash Flow, etc.)
- Last 4 annual periods from SEC aggregated data
- SIMPLIFIED only - use fetch_filing() for detailed analysis

**5. `insider_activity(ticker, days=30)` - OWNERSHIP FORMS**
- Forms 3/4/5/144 for a ticker, series-aggregated per filer (the signal is the
  SERIES — single-filing reads miss sustained distribution)
- Per filing: filer, position, code, shares, price, post-holdings,
  **aff10b5One** (10b5-1 checkbox, parsed from raw XML — edgartools drops it),
  footnotes, acceptance time
- Form 144 rows are PROPOSED sales (notices); Form 4/5 rows are executions
- Per-filer SERIES rollup: gross sold/bought + discretionary-vs-plan status

### Cache Strategy

**Default Location**: `/var/idio-mcp-cache/sec-filings/`
**Configurable**: Set `CACHE_DIR` environment variable

**Organization**: `/{TICKER|CIK}/{FORM}/{YYYY-MM-DD}-{ACCESSION}.{ext}` — primary
documents. The accession number is what makes the path unique: a company files
more than once a day routinely, so a date-keyed path addresses two filings at
once and serves whichever was written first
**Accession documents**: `/{TICKER|CIK}/{FORM}/{YYYY-MM-DD}-{ACCESSION}/{document}`
— one level down, because `list_all()` reads a filename stem as a filing and a
document stored flat would surface as one
**Formats**: `.txt` (preferred), `.md`, `.html`, `.xml` (raw passthrough for ownership forms)

---

## BBG Lite Design System

**Six Principles**:

1. **Hierarchy through layout** - Position and spacing convey importance
2. **Density without clutter** - Every character earns its keep
3. **Consistency breeds speed** - Similar data looks similar
4. **Context is self-evident** - Outputs understandable in isolation
5. **Progressive disclosure** - Summary first, depth via separate calls
6. **Guidance in context** - Show what's possible next (affordances)

**Example Output**:

```
TSLA 10-K FILINGS AVAILABLE
──────────────────────────────────────────────────────────────────────
FILED       CACHED  SIZE     [ACTIONS]
2025-04-30  ✓       423 KB
2025-01-30  ✓       313 KB
2024-01-29  ✓       814 KB
2023-01-31          -

Showing 15 of 20 filings (3 cached)

Try: fetch_filing(ticker, form, date) | search_filing(ticker, form, pattern)
```

**Key Features**:
- Dense, scannable tables
- Clear section headers
- Affordances ("Try:") show next steps
- Line numbers match Read tool format (6 digits + →)
- Professional, terminal-friendly aesthetic

---

## Development Workflow

### Quick Start

```bash
# Install dependencies
poetry install

# Run CLI (fast iteration, no server restart)
./cli list-tools                        # Show MCP tool definitions
./cli list-filings TSLA 10-K            # List available filings
./cli fetch TSLA 10-K                   # Fetch with preview
./cli search TSLA 10-K "vehicle"        # Search filing
./cli documents NVDA 13F-HR             # What's in the accession?
./cli fetch NVDA 13F-HR --document 2    # Fetch a named doc from the accession
./cli holdings 1082621                  # 13F holdings by CIK (no ticker exists)

# Run MCP server (for Claude Code integration)
make server                             # Start in background (port 5012 dev, 5002 prod)
make logs                               # Tail server logs

# Development mode (auto-restart on file changes)
make dev                                # Server restarts when you edit files
```

### Testing Pattern

**1. Test CLI first** (fastest iteration):
```bash
./cli fetch TSLA 10-K --date 2024-01-29
./cli search TSLA 10-K "supply chain" --context 3
```

**2. Test MCP server** (via stdio transport):
```bash
poetry run python -m mcp_edgar_ux.server --transport stdio
# Test with MCP inspector or Claude Code
```

**3. Test HTTP/SSE server** (for web integration):
```bash
poetry run uvicorn mcp_edgar_ux.server_http:app --host 127.0.0.1 --port 5012
curl http://127.0.0.1:5012/health
```

### Code Quality

**Before committing**:
```bash
# Format
poetry run black mcp_edgar_ux/

# Type check
poetry run mypy mcp_edgar_ux/

# Lint
poetry run ruff check mcp_edgar_ux/
```

### Cache Configuration

**Default**: `/var/idio-mcp-cache/sec-filings/`
**Override**: Set `CACHE_DIR` environment variable

```bash
# Test with isolated cache
CACHE_DIR=/tmp/sec-filings-test ./cli fetch TSLA 10-K
```

---

## Tool Design Principles

### "UI not API" Principle

**Philosophy**: Tools return human-readable formatted output, not raw data

**Pattern**:
- Same output humans see = same output AI sees
- Iterate tool design based on human validation
- If formatted output is useful to humans → useful to AI

### Progressive Disclosure

**Philosophy**: Don't dump entire 200-page filing into context

**Pattern**:
- Return path + preview (first 50 lines)
- User/AI decides what to read next (Read/Grep/Bash)
- This scales: filing size doesn't matter

### Affordances

**Philosophy**: Every tool output shows "Try: ..." with next steps

**Example**: `Try: search_filing("TSLA", "10-K", "SEARCH TERM")`

**Why**: Guides AI toward effective research workflows

---

## Implementation Status

**Architecture**: Hexagonal (Ports & Adapters) ✅
- Clean separation: core → adapters → servers
- Dependency injection via Container
- 81% reduction in server code (886 → 170 lines)
- Eliminated ~300 lines of duplication

**Core Features**: Complete ✅
- Async architecture with asyncio.to_thread()
- SEC API integration (historical + current filings)
- Fetch filing (returns path, saves to disk)
- Search filing (ugrep-based with fuzzy matching, line numbers)
- List filings (discovery + cached status)
- Financial statements (simplified GAAP metrics)

**MCP Integration**: Complete ✅
- HTTP/SSE server (170 lines, port 5012 dev / 5002 prod)
- Seven tools: fetch_filing, search_filing, list_filings, list_documents,
  thirteenf_holdings, get_financial_statements, insider_activity
- Shared tool definitions (DRY)
- BBG Lite formatted output

**CLI**: Complete ✅
- Commands: list-tools, fetch, search, list-filings, documents, holdings, insider, financials
- Uses same hexagonal core as MCP server
- Fast iteration without server restart

**Testing**: Partial
- CLI tested and working ✅
- `tests/test_accession_documents.py` — CIK addressing, omitted-document rule,
  13F reconciliation, cache paths, PARTIAL formatting (22 tests, no network) ✅
- `tests/test_hexagonal.py` — hexagonal core contracts, including
  `TestCoreFormTypes`, which asserts the deliberate CORE membership choices:
  `SC 13D` excluded, `6-K` included, `S-3ASR` included ✅
- `tests/test_cache_addressing.py` — drives the real `FetchFilingService`
  against the real `FilesystemCache` with a fake SEC. Covers two filings on one
  date, cache hits for a CIK-addressed filer, and disk accounting ✅
- 45 tests, all passing, **none of which touch the network**. There is still no
  coverage of a real SEC response, and no CI — nothing runs the suite on push —
  and `make lint` cannot run because mypy and ruff are absent from the dev
  dependencies.

---

## Philosophy

### KISS (Keep It Simple, Stupid)
- Python is appropriate (network I/O is bottleneck, not Python)
- edgartools is too valuable to replace (handles SEC quirks)
- Text format scales (vs. clever markdown with XBRL issues)

### Detective Debugging
- Investigate first, implement second
- Form theories, gather evidence from logs/errors
- Follow the data, don't rush to code

### Hexagonal Architecture (Ports & Adapters)
- `core/` = Pure business logic (domain models, ports, services)
- `adapters/` = Infrastructure implementations (filesystem, edgar, search, mcp)
- `container.py` = Dependency injection (wires everything together)
- `server_http.py` = MCP protocol delivery (170 lines, thin wrapper)
- `cli.py` = Testing interface (uses same container as server)

### DRY (Don't Repeat Yourself)
- Single BBG Lite formatter per output type
- Formatters shared between CLI and MCP server
- One true path: asyncio.to_thread() for all blocking I/O

### The Bitter Lesson (Rich Sutton)
- Scale > cleverness
- Build systems that improve with data/compute
- Don't dump 241K tokens into context
- Save to disk, read what you need
- [The Bitter Lesson](http://www.incompleteideas.net/IncIdeas/BitterLesson.html)

---

## Common Patterns

### Adding a New Tool

1. **Add service to core/services.py** (business logic):
```python
class NewToolService:
    def __init__(self, repository: FilingRepository, fetcher: FilingFetcher):
        self.repository = repository
        self.fetcher = fetcher

    def execute(self, param: str) -> dict:
        # Business logic here
        return {"success": True, "data": ...}
```

2. **Wire up in container.py** (dependency injection):
```python
class Container:
    def __init__(self, cache_dir, user_agent):
        # ... existing code ...
        self.new_tool = NewToolService(
            repository=self.cache,
            fetcher=self.fetcher
        )
```

3. **Add tool definition to adapters/mcp/tool_definitions.py**:
```python
TOOL_SCHEMAS["new_tool"] = {
    "name": "new_tool",
    "description": "Clear description with examples",
    "inputSchema": {...}
}
```

4. **Add handler to adapters/mcp/handlers.py**:
```python
async def new_tool(self, param: str) -> dict:
    result = await asyncio.to_thread(
        self.container.new_tool.execute,
        param=param
    )
    return result
```

5. **Add to server_http.py** (MCP tool registration):
```python
@mcp_server.list_tools()
async def list_tools():
    return [
        # ... existing tools ...
        Tool(**TOOL_SCHEMAS["new_tool"]),
    ]

@mcp_server.call_tool()
async def call_tool(name: str, arguments: dict):
    # ... existing tools ...
    elif name == "new_tool":
        result = await handlers.new_tool(...)
```

6. **Add CLI command** (in cli.py):
```python
async def new_tool_command(...) -> int:
    container = Container(cache_dir=cache_dir, user_agent=get_user_agent())
    handlers = MCPHandlers(container)
    result = await handlers.new_tool(...)
    print(json.dumps(result, indent=2))
    return 0
```

7. **Test via CLI first**:
```bash
./cli new-tool PARAM
```

### Debugging Async Issues

**Pattern**: Use print statements with flush=True
```python
print(f"[DEBUG] Starting operation: {param}", flush=True)
result = await asyncio.to_thread(blocking_function, param)
print(f"[DEBUG] Completed: {result}", flush=True)
```

**Why**: Async can interleave logs, flush=True ensures immediate output

### BBG Lite Formatting Checklist

- [ ] Dense, scannable layout
- [ ] Clear section headers (uppercase + separators)
- [ ] Consistent column widths
- [ ] Line numbers (if showing file content): `  1234→content`
- [ ] Affordances ("Try:") at end
- [ ] Professional tone
- [ ] Context-independent (readable in isolation)

---

## Future Enhancements (Post-MVP)

**FAISS Indexing**:
- Semantic search across filings
- "Find all mentions of supply chain risk across 10 years of 10-Ks"
- Background indexing when filing cached

**Cross-Filing Analysis**:
- Search all TSLA filings at once
- Track narrative changes over time

**Trend Detection**:
- Compare 10-K sections year-over-year
- Flag new risk disclosures
- Track management tone shifts

---

## Key Files

**Core Implementation** (Hexagonal Architecture):
- `mcp_edgar_ux/core/domain.py` - Domain models (Filing, SearchResult, etc.)
- `mcp_edgar_ux/core/ports.py` - Port interfaces (Repository, Fetcher, Searcher)
- `mcp_edgar_ux/core/services.py` - Use cases (business logic)
- `mcp_edgar_ux/adapters/filesystem.py` - Filesystem cache adapter
- `mcp_edgar_ux/adapters/edgar.py` - EDGAR API adapter
- `mcp_edgar_ux/adapters/search.py` - Grep search adapter
- `mcp_edgar_ux/adapters/mcp/tool_definitions.py` - Shared MCP tool schemas
- `mcp_edgar_ux/adapters/mcp/handlers.py` - Shared MCP handlers
- `mcp_edgar_ux/container.py` - Dependency injection container
- `mcp_edgar_ux/server_http.py` - MCP HTTP/SSE server (170 lines)
- `mcp_edgar_ux/cli.py` - CLI for testing

**Documentation**:
- `README.md` - User documentation, installation, usage
- `DEVELOPER.md` - This file (architecture, patterns, development)

**Development**:
- `Makefile` - All development commands (dev, server, logs, test, lint)
- `cli` - Wrapper script for CLI

**Configuration**:
- `pyproject.toml` - Poetry dependencies
- `.gitignore` - Excludes logs/, __pycache__, etc.

---

## MCP Protocol Notes

**Transports Supported**:
- `stdio` - Standard input/output (for Claude Code integration)
- `streamable-http` - HTTP/SSE (for web integration)

**Tool Discovery**:
- MCP clients call `list_tools()` to discover available tools
- Returns Tool objects with name, description, inputSchema

**Tool Execution**:
- Client calls `call_tool(name, arguments)`
- Server executes tool, returns list of TextContent
- All output is human-readable formatted text (BBG Lite)

**Error Handling**:
- Tools return `{"success": False, "error": "message"}` on failure
- Formatters display errors in consistent format
- No exceptions leak to client

---

## Contributing

**Before submitting PR**:
1. Test via CLI: `./cli <command>` works
2. Code quality: `poetry run black . && poetry run mypy . && poetry run ruff check .`
3. Update TASKS.md if adding features
4. Add examples to tool descriptions
5. Ensure BBG Lite formatting consistency

**Design Principles to Follow**:
- Design for humans, AI benefits automatically
- Progressive disclosure (summary first, depth via separate calls)
- Affordances (show next steps in output)
- The Bitter Lesson (scale > cleverness)
