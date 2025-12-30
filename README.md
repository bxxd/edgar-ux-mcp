# mcp-edgar-ux

**For AI agents:** SEC EDGAR filings that won't blow your context window. I return file paths, you use Read/Grep/Bash. A Tesla 10-K is 241K tokens - I save it to disk so you read only what you need.

**For humans:** Bloomberg Terminal-style output. Formatted tables, not raw JSON. Context-efficient by design.

## Why This Approach Wins

**Other SEC MCPs:** Dump entire filing into tool response (241K tokens per 10-K)

**This MCP:** Save to `/var/idio-mcp-cache/sec-filings/`, return path (50 tokens)

You get:
- **Zero context pollution** - Filing doesn't count against your limit
- **Selective reading** - Read line 1000-1050, not all 10,000 lines
- **Multi-filing analysis** - Compare 5 years of 10-Ks without context overflow
- **Formatted discovery** - BBG Lite tables show what's available, not JSON blobs

The Bitter Lesson: Scale (disk storage) beats cleverness (cramming into context).

## Installation

```bash
poetry install
```

## Usage

### Quick Start

```bash
# Development (auto-reload on file changes, port 5012)
make dev        # Server restarts when you edit code

# Production (background daemon, port 5002)
make server     # Start server on http://127.0.0.1:5002
make logs       # Tail server logs

# stdio mode (for Claude Code)
make stdio      # Start stdio server

# Configure via .env (optional)
cp .env.example .env
# Edit .env to customize PORT and CACHE_DIR
```

**Default ports:**
- Development: 5012 (auto-reload with `make dev`)
- Production: 5002 (background daemon with `make server`)

### Configure Claude Code

**Option 1: Using Poetry (recommended)**

Configure Claude Code to use stdio transport:

```json
{
  "projects": {
    "/your/project/path": {
      "mcpServers": {
        "mcp-edgar-ux": {
          "command": "poetry",
          "args": ["run", "mcp-edgar-ux"],
          "cwd": "/path/to/mcp-edgar-ux"
        }
      }
    }
  }
}
```

**Option 2: SSE/HTTP Server (for web-based deployments)**

For web interfaces or when you need a persistent HTTP server:

```bash
# Start HTTP server (runs in background)
make server

# Tail logs
make logs

# Customize port via .env (see Configuration section below)
```

Configure Claude Code to use SSE transport:

```json
{
  "projects": {
    "/path/to/your/project": {
      "mcpServers": {
        "edgar-ux": {
          "type": "sse",
          "url": "http://127.0.0.1:5002/sse"
        }
      }
    }
  }
}
```

**When to use SSE:**
- Web-based Claude Code deployments (e.g., browser terminals)
- Multiple clients sharing one server instance
- Debugging without restarting Claude Code

**When to use stdio:**
- Local CLI usage (standard approach)
- Single-user development environment

**Usage in Claude Code:**

```bash
# Fetch a filing
fetch_filing("TSLA", "10-K")
→ {path: "/var/idio-mcp-cache/sec-filings/TSLA/10-K/2025-04-30.txt", ...}

# Read what you need
Read("/var/idio-mcp-cache/sec-filings/TSLA/10-K/2025-04-30.txt", offset=1200, limit=50)

# Search for terms
Grep("supply chain", path="/var/idio-mcp-cache/sec-filings/TSLA/10-K/2025-04-30.txt")
```

## Tools

### `fetch_filing(ticker, form_type, date=None, format="text")`

Download SEC filing to disk, return path.

**Args:**
- `ticker`: Stock ticker (e.g., "TSLA", "AAPL")
- `form_type`: Form type ("10-K", "10-Q", "8-K", etc.)
- `date`: Optional date filter (YYYY-MM-DD). Returns filing closest >= date.
- `format`: Output format - "text" (default, clean), "markdown" (may have XBRL), or "html"

**Returns:**
```json
{
  "success": true,
  "path": "/var/idio-mcp-cache/sec-filings/TSLA/10-K/2025-04-30.txt",
  "company": "Tesla, Inc.",
  "ticker": "TSLA",
  "form_type": "10-K",
  "filing_date": "2025-04-30",
  "format": "text",
  "size_bytes": 427000,
  "sec_url": "https://www.sec.gov/...",
  "cached": false
}
```

**Examples:**
```python
# Latest filing (text format, clean)
fetch_filing("TSLA", "10-K")

# Filing on or after specific date
fetch_filing("TSLA", "10-K", date="2024-01-01")

# Markdown format (may contain XBRL artifacts)
fetch_filing("AAPL", "10-Q", format="markdown")
```

### `search_filing(ticker, form_type, pattern, ...)`

Search for pattern in SEC filing with fuzzy matching (tolerates typos/variations).

**Args:**
- `ticker`: Stock ticker (e.g., "TSLA", "AAPL")
- `form_type`: Form type ("10-K", "10-Q", "8-K", etc.)
- `pattern`: Search pattern (extended regex, case-insensitive, fuzzy=1)
- `date`: Optional date filter (YYYY-MM-DD)
- `context_lines`: Lines of context before/after match (default: 2)
- `max_results`: Maximum matches to return (default: 20)

**Returns:** Matches with line numbers and surrounding context.

**Examples:**
```python
# Find supply chain mentions
search_filing("TSLA", "10-K", "supply chain")
→ Finds: "supply chain", "supply-chain", "Supply Chain" (fuzzy matching)

# Search for multiple terms (OR)
search_filing("LNG", "10-Q", "Corpus Christi|Stage 3")
→ Matches either term
```

### `list_filings(form_type, ticker=None, ...)`

List available SEC filings and their cached status.

**Args:**
- `form_type`: Form type (e.g., "10-K", "10-Q", "8-K")
- `ticker`: Optional stock ticker. Omit to see latest across all companies.
- `start`: Starting index (default: 0, newest first)
- `max`: Maximum filings to return (default: 15)

**Returns:** List of filings with cached status (✓ = cached locally).

### `get_financial_statements(ticker, statement_type="all")`

Get simplified financial statements (key metrics only, last 4 years).

**IMPORTANT:** Returns SIMPLIFIED high-level metrics from SEC aggregated data.
For detailed analysis, use `fetch_filing()` to get the full 10-K/10-Q.

**Args:**
- `ticker`: Stock ticker (e.g., "TSLA", "AAPL")
- `statement_type`: "all" (default), "income", "balance", or "cash_flow"

**Returns:** Formatted multi-year statements (income, balance sheet, cash flow).

**What you get:**
- Key GAAP metrics: Revenue, Net Income, Assets, Cash Flow, etc.
- Last 4 annual periods
- Clean, formatted tables

**What you DON'T get:**
- Footnotes, exhibits, MD&A
- Non-GAAP metrics or detailed line items
- Forward-looking statements

**Examples:**
```python
# All statements (4 years)
get_financial_statements("TSLA")

# Income statement only
get_financial_statements("TSLA", statement_type="income")
```

## Example Output

**Our differentiator: BBG Lite formatted, human-readable output**

### list_filings("TSLA", "10-K")

```
TSLA 10-K FILINGS AVAILABLE
──────────────────────────────────────────────────────────────────────
FILED       CACHED  SIZE     [ACTIONS]
2025-04-30  ✓       423 KB
2025-01-30  ✓       313 KB
2024-01-29  ✓       814 KB
2023-01-31          -
2022-05-02          -
2022-02-07          -
2021-04-30          -
2021-02-08          -
2020-04-28          -
2020-02-13          -
2019-02-19          -
2018-02-23          -
2017-03-01          -

... 5 more filings

──────────────────────────────────────────────────────────────────────
✓ Cached filings available locally (instant access)
  Other filings will be downloaded on demand from SEC

Data source: SEC EDGAR | Powered by edgartools
```

### fetch_filing("TSLA", "10-K")

```
TSLA 10-K | 2025-04-30 | FETCHED (downloaded)

COMPANY:     Tesla, Inc.
FORM:        10-K
FILED:       2025-04-30
SIZE:        427 KB (10,234 lines)

PATH: /var/idio-mcp-cache/sec-filings/TSLA/10-K/2025-04-30.txt

Try: Read(path, offset=0, limit=50) | search_filing("TSLA", "10-K", "SEARCH TERM")
```

Clean, formatted, immediately useful. No raw JSON dumps, no 241K tokens in context.

## Configuration

**Using .env file (recommended):**

```bash
# Copy example and customize
cp .env.example .env

# Edit .env
PORT=5002
CACHE_DIR=/var/idio-mcp-cache/sec-filings
```

**Or override inline:**

```bash
# Custom port
PORT=8080 make server

# Custom cache directory
CACHE_DIR=/custom/path make server
```

**Defaults (if no .env):**
- Port: `5002`
- Cache: `/var/idio-mcp-cache/sec-filings`
- User agent: `breed research breed@idio.sh` (SEC requires this)

## Workflow Example

```bash
# 1. Fetch Tesla's latest 10-K
fetch_filing("TSLA", "10-K")
→ /var/idio-mcp-cache/sec-filings/TSLA/10-K/2025-04-30.txt (427KB, clean text)

# 2. Search for supply chain mentions
search_filing("TSLA", "10-K", "supply chain")
→ Shows matches with line numbers and context

# 3. Read specific section
Read("/var/idio-mcp-cache/sec-filings/TSLA/10-K/2025-04-30.txt", offset=1200, limit=50)
→ Only 50 lines in context (not 241K tokens)

# 4. Analyze
"What are Tesla's supply chain risks?"
```

## Why File-Based?

**Problem:** Current MCPs dump full filing into tool response
- TSLA 10-K = 241,120 tokens
- AAPL 10-K = 268,922 tokens
- Blows through context window
- Forces LLM to process everything

**Solution:** Save to disk, read selectively
- Zero context pollution on fetch
- Use Read/Grep to view exactly what you need
- Can work with multiple filings simultaneously
- Filings persist between sessions

**The Bitter Lesson:** Scale (disk) beats cleverness (context).

## Development

```bash
# Install dependencies
poetry install

# Development mode (auto-reload on file changes)
make dev

# Run tests
make test

# Lint and type check
make lint

# Clean cache
make clean
```

## Architecture

**Hexagonal Architecture (Ports & Adapters):**

- `mcp_edgar_ux/core/` - Business logic
  - Domain models (Filing, SearchResult, etc.)
  - Port interfaces (Repository, Fetcher, Searcher)
  - Use case services (pure business logic)

- `mcp_edgar_ux/adapters/` - Infrastructure
  - Filesystem cache (implements Repository port)
  - EDGAR API client (implements Fetcher port)
  - Grep search (implements Searcher port)
  - MCP handlers (shared tool definitions)

- `mcp_edgar_ux/container.py` - Dependency injection
  - Wires adapters to core services
  - Single point of configuration

- `mcp_edgar_ux/server_http.py` - MCP HTTP/SSE server (170 lines)
  - Thin wrapper around core
  - Uses dependency injection

**Benefits:**
- Core is testable without MCP or infrastructure
- Can swap adapters (S3 cache, different SEC API, etc.)
- 81% reduction in server code via dependency injection
- Eliminated ~300 lines of duplication

## Credits

Inspired by [sec-edgar-mcp](https://github.com/stefanoamorelli/sec-edgar-mcp)
Built with [edgartools](https://github.com/dgunning/edgartools)
Named after [The Bitter Lesson](http://www.incompleteideas.net/IncIdeas/BitterLesson.html) by Rich Sutton

## License

MIT
