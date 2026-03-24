# samply-mcp

An MCP (Model Context Protocol) server that wraps samply for safe, AI-agent-driven profiling of native binaries on Linux.

## Purpose

Enables AI coding agents to profile native applications using samply without requiring root privileges or exposing a web server. The server manages profiling sessions that require explicit human approval via a terminal REPL.

## Key Commands

```bash
# Run linter
uv run ruff check .

# Run type checker
uv run ty check src tests

# Run all tests
uv run pytest tests/ -v

# Run tests with coverage
uv run pytest tests/ --cov=src/samply_mcp --cov-report=term-missing

# Start the MCP server (production)
uv run samply-mcp --port 7837

# Start with auto-approve (dangerous, for testing only)
uv run samply-mcp --port 7837 --dangerously-auto-approve-sessions
```

## Architecture

- **MCP Server** (`server.py`) - FastMCP server exposing profiling tools to AI agents
- **REPL** (`repl.py`) - Terminal interface for human approval of sessions
- **Session Manager** (`session_manager.py`) - SQLite-backed session persistence
- **Runner** (`runner.py`) - samply execution with setup scripts, timeouts, output capture
- **Gecko Parser** (`gecko/profile.py`) - Parses samply's Gecko JSON profiles
- **Symbolizer** (`gecko/symbolizer.py`) - Resolves addresses to symbols using addr2line

## Prerequisites

- Linux only
- `kernel.perf_event_paranoid ≤ 1` (check with `cat /proc/sys/kernel/perf_event_paranoid`)
- `samply` installed
- `addr2line` installed

## Profile Format

samply outputs Gecko JSON profiles. The parser aggregates samples across all threads and resolves symbols via addr2line. Each thread has its own string/frame/stack tables, so aggregation is by resolved function name.

## Session Flow

1. Agent calls `create_session` with binary path, args, optional setup script
2. Server creates pending session, prints approval request to terminal
3. Human reviews and approves via REPL: `approve <session_id> <hash>`
4. Agent calls `run` to execute samply profiling
5. Agent queries results: `get_hot_functions`, `get_callers`, `compare_runs`, etc.
