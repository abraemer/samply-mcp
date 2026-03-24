# samply-mcp

An MCP (Model Context Protocol) server that enables AI coding agents to profile native binaries using [samply](https://github.com/mstange/samply) safely, without requiring root privileges or exposing a web server.

## Why?

AI coding agents can optimize code, but they need profiling data to know *what* to optimize. samply-mcp bridges this gap by:

- **No root required**: The kernel parameter for `perf_event_open` is set once by you, not by the agent
- **Human approval flow**: Agents cannot execute arbitrary binaries without your explicit approval
- **Structured output**: Complex Gecko profile JSON is parsed into queryable semantic results

## Prerequisites

- **Linux only** (macOS is not supported)
- `kernel.perf_event_paranoid ≤ 1` (check with `cat /proc/sys/kernel/perf_event_paranoid`)
- [samply](https://github.com/mstange/samply) installed (`cargo install samply`)
- `addr2line` installed (usually part of `binutils`)

### Setting up kernel.perf_event_paranoid

```bash
sudo sysctl -w kernel.perf_event_paranoid=1
```

To make it permanent:

```bash
echo 'kernel.perf_event_paranoid=1' | sudo tee /etc/sysctl.d/99-perf.conf
sudo sysctl --system
```

## Installation

```bash
git clone https://github.com/abraemer/samply-mcp.git
cd samply-mcp
uv sync
```

## Usage

### Starting the server

```bash
uv run samply-mcp --port 7837
```

For testing or trusted environments, you can enable auto-approval (use with caution):

```bash
uv run samply-mcp --port 7837 --dangerously-auto-approve-sessions
```

### Connecting your AI agent

Configure your MCP client (Claude Desktop, Cursor, etc.) to connect to `http://localhost:7837`.

### Workflow

1. **Agent creates a session**: `create_session(binary_path, args, working_directory)`
2. **You approve in the terminal**: The server prints session details, you review and approve with `approve <session_id> <hash>`
3. **Agent runs profiling**: `run(session_id)` executes samply
4. **Agent analyzes results**: `get_hot_functions()`, `get_callers()`, `get_callees()`, `compare_runs()`

### Terminal REPL Commands

| Command | Description |
|---------|-------------|
| `sessions` | List all sessions and their states |
| `show <session_id>` | Show full session details |
| `approve <session_id> <hash>` | Approve a pending session |
| `reject <session_id>` | Reject and destroy a pending session |
| `runs <session_id>` | List all runs for a session |
| `help` | Show available commands |
| `quit` | Stop the server |

## MCP Tools

| Tool | Description |
|------|-------------|
| `create_session` | Create a profiling session (requires approval) |
| `get_session_status` | Get current session state and details |
| `destroy_session` | Remove a session |
| `list_sessions` | List all sessions |
| `run` | Execute a profiling run |
| `get_run_summary` | Get overview of a run with top 5 hot functions |
| `get_hot_functions` | Get top N functions by self-time |
| `get_callers` | Find who calls a function |
| `get_callees` | Find what a function calls |
| `compare_runs` | Compare two runs to see improvements/regressions |

## Data Storage

- Session metadata: `~/.local/share/samply-mcp/sessions.db` (SQLite)
- Profile files: `~/.local/share/samply-mcp/profiles/<session_id>/<run_id>.json`

## Development

```bash
# Run linter
uv run ruff check .

# Run type checker
uv run ty check src tests

# Run tests
uv run pytest tests/ -v

# Run tests with coverage
uv run pytest tests/ --cov=src/samply_mcp --cov-report=term-missing
```

## License

MIT
