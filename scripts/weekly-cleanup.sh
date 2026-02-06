#!/bin/bash
# Weekly cleanup for mcp-edgar-ux
# Clears edgartools HTTP cache (grows unbounded over time)
# Restarts server to reclaim memory
#
# Cron: 0 4 * * 0 /home/ubuntu/idio/dev/mcp-edgar-ux/scripts/weekly-cleanup.sh
# (Every Sunday at 4am)

set -e

export PATH="$HOME/.local/bin:$PATH"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
LOG="$PROJECT_DIR/logs/cleanup.log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG"
}

log "=== Weekly cleanup started ==="

# 1. Clear edgartools HTTP cache (files older than 7 days)
CACHE_DIR="$HOME/.edgar/_tcache"
if [ -d "$CACHE_DIR" ]; then
    BEFORE=$(du -sh "$CACHE_DIR" 2>/dev/null | cut -f1)
    find "$CACHE_DIR" -type f -mtime +7 -delete 2>/dev/null || true
    find "$CACHE_DIR" -type d -empty -delete 2>/dev/null || true
    AFTER=$(du -sh "$CACHE_DIR" 2>/dev/null | cut -f1)
    log "Cleared cache: $BEFORE -> $AFTER"
else
    log "Cache dir not found: $CACHE_DIR"
fi

# 2. Restart dev server
cd "$PROJECT_DIR"
if [ -f logs/server.pid ]; then
    PID=$(cat logs/server.pid)
    if kill -0 "$PID" 2>/dev/null; then
        kill "$PID"
        sleep 2
        log "Killed old server (PID $PID)"
    fi
fi
make server >> "$LOG" 2>&1
log "Dev server restarted"

# 3. Restart prod server (if this is being run from dev)
PROD_DIR="/home/ubuntu/idio/prod/mcp-edgar-ux"
if [ -d "$PROD_DIR" ]; then
    cd "$PROD_DIR"
    if [ -f logs/server.pid ]; then
        PID=$(cat logs/server.pid)
        if kill -0 "$PID" 2>/dev/null; then
            kill "$PID"
            sleep 2
            log "Killed old prod server (PID $PID)"
        fi
    fi
    make server >> "$LOG" 2>&1
    log "Prod server restarted"
fi

log "=== Weekly cleanup completed ==="
