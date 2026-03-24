"""FastMCP server and tool definitions."""

import argparse
import platform
import shutil
import stat
import sys
import threading
from datetime import datetime
from pathlib import Path

from fastmcp import FastMCP

from samply_mcp.gecko.parser import parse_gecko_profile
from samply_mcp.gecko.profile import GeckoProfile, ParseError
from samply_mcp.gecko.symbolizer import Symbolizer
from samply_mcp.repl import REPL, notify_session_created
from samply_mcp.runner import execute_setup_script, run_samply
from samply_mcp.session import OutputMode, Run
from samply_mcp.session_manager import SessionManager

mcp = FastMCP(
    "samply-mcp",
    instructions="""
Profile native binaries with samply in a session-based flow.
To get started use create_session.

WORKFLOW
========
1. create_session(binary_path, args) -> wait for human approval
2. run(session_id) -> profile the binary
3. get_hot_functions() -> identify performance hotspots
4. get_callers(name)/get_callees(name) -> understand call relationships
5. Make optimizations, run again, compare_runs() to measure improvement

ANALYSIS TIPS
=============
- self_pct: time in function itself (optimize algorithm)
- total_pct: time including callees (investigate callees)
- Generally, it is better to optimize the algorithm first. This means starting at a hot function
  and walking up the call stack, looking for opportunities to reduce calls to the hot function
  by changing the algorithm, e.g. using memoization, precomputation, more sophisticated algorithms.
- Once the algorithm is optimized, try to optimize closer to the hot function and think
  about micro-optimizations.
""",
)
_manager: SessionManager | None = None
_auto_approve: bool = False


def _get_manager() -> SessionManager:
    global _manager
    if _manager is None:
        _manager = SessionManager()
    return _manager


def _is_executable(path: Path) -> bool:
    if not path.exists():
        return False
    if not path.is_file():
        return False
    mode = path.stat().st_mode
    return bool(mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))


def check_platform() -> bool:
    """Check if running on Linux. Returns True if Linux, False otherwise."""
    return platform.system() == "Linux"


def check_samply_installed() -> bool:
    """Check if samply is installed and accessible in PATH."""
    return shutil.which("samply") is not None


def check_addr2line_installed() -> bool:
    """Check if addr2line is installed and accessible in PATH."""
    return shutil.which("addr2line") is not None


def check_perf_paranoid() -> tuple[bool, int]:
    """
    Check kernel.perf_event_paranoid level.

    Returns:
        tuple: (is_ok, level) where is_ok is True if level <= 1
    """
    try:
        paranoid_path = Path("/proc/sys/kernel/perf_event_paranoid")
        level = int(paranoid_path.read_text().strip())
        return (level <= 1, level)
    except (OSError, ValueError):
        return (False, -1)


def validate_startup() -> None:
    """
    Validate all startup prerequisites.

    Exits with an error message if any prerequisite is not met.
    """
    if not check_platform():
        print("Error: samply-mcp only supports Linux.", file=sys.stderr)
        print("macOS is not supported due to differences in profiling mechanisms.", file=sys.stderr)
        sys.exit(1)

    if not check_samply_installed():
        print("Error: samply is not installed or not in PATH.", file=sys.stderr)
        print("\nTo install samply:", file=sys.stderr)
        print("  cargo install samply", file=sys.stderr)
        print("\nOr visit: https://github.com/mstange/samply", file=sys.stderr)
        sys.exit(1)

    if not check_addr2line_installed():
        print("Error: addr2line is not installed or not in PATH.", file=sys.stderr)
        print("\naddr2line is typically part of the binutils package.", file=sys.stderr)
        print("To install on Debian/Ubuntu:", file=sys.stderr)
        print("  sudo apt install binutils", file=sys.stderr)
        print("\nTo install on Fedora/RHEL:", file=sys.stderr)
        print("  sudo dnf install binutils", file=sys.stderr)
        sys.exit(1)

    is_ok, level = check_perf_paranoid()
    if not is_ok:
        print(f"Error: kernel.perf_event_paranoid is {level}, but must be <= 1.", file=sys.stderr)
        print("\nTo fix this, run:", file=sys.stderr)
        print("  sudo sysctl -w kernel.perf_event_paranoid=1", file=sys.stderr)
        print("\nTo make it permanent:", file=sys.stderr)
        print(
            "  echo 'kernel.perf_event_paranoid=1' | sudo tee /etc/sysctl.d/99-perf.conf",
            file=sys.stderr,
        )
        print("  sudo sysctl --system", file=sys.stderr)
        sys.exit(1)


@mcp.tool()
async def create_session(
    binary_path: str,
    args: list[str],
    working_directory: str,
    setup_script_path: str | None = None,
    env: dict | None = None,
) -> dict:
    """Create a profiling session for a binary.

    Depending on the settings, the session must be approved once by the user
    via the REPL before profiling can be run.
    The setup script is run everytime before a profile is taken and should
    include things like:
     - set up clean data to run on (if applicable)
     - recompile binary (if applicable)

    Args:
        binary_path: Path to the executable to profile.
        args: Command-line arguments to pass to the binary.
        working_directory: Working directory for the binary. This is typically
            the agent's project root, where relative paths in args (like data
            files) are resolved from.
        setup_script_path: Optional path to a setup script to run before profiling.
        env: Optional environment variables for the binary.

    Returns:
        dict with session_id, command_hash, display_command, status, and next_steps.
    """
    binary = Path(binary_path)
    if not binary.exists():
        return {"error": f"Binary path does not exist: {binary_path}"}
    if not _is_executable(binary):
        return {"error": f"Binary path is not executable: {binary_path}"}

    working_dir = Path(working_directory)
    if not working_dir.exists():
        return {"error": f"Working directory does not exist: {working_directory}"}

    setup_script = Path(setup_script_path) if setup_script_path else None
    if setup_script is not None:
        if not setup_script.exists():
            return {"error": f"Setup script path does not exist: {setup_script_path}"}
        if not _is_executable(setup_script):
            return {"error": f"Setup script path is not executable: {setup_script_path}"}

    manager = _get_manager()
    session = manager.create_session(
        binary_path=binary,
        args=args,
        setup_script_path=setup_script,
        env=env,
        working_directory=working_dir,
        auto_approve=_auto_approve,
    )

    if session.state.value == "pending_approval":
        notify_session_created(session)

    display_cmd = binary_path
    if args:
        display_cmd += " " + " ".join(args)

    need_approval = session.state.value == "pending_approval"
    if need_approval:
        next_steps = """
The user needs to approve the session. Tell them. 
Then you can start profiling with the run tool.
"""
    else:
        next_steps = """
No approval necessary.
Start profiling with the run tool.
"""

    return {
        "session_id": session.id,
        "command_hash": session.command_hash,
        "display_command": display_cmd,
        "setup_script": session.setup_script_snapshot,
        "status": session.state.value,
        "next_steps": next_steps,
    }


@mcp.tool()
async def get_session_status(session_id: str) -> dict:
    """Get the status of a session.

    Args:
        session_id: The session ID to query.

    Returns:
        dict with session details including state, binary_path, args, and runs.
    """
    manager = _get_manager()
    return manager.get_session_status(session_id)


@mcp.tool()
async def destroy_session(session_id: str, delete_profiles: bool = False) -> dict:
    """Destroy a session.

    Args:
        session_id: The session ID to destroy.
        delete_profiles: If True, delete all profile files for this session.

    Returns:
        dict with session_id and destroyed=True on success, or error on failure.
    """
    manager = _get_manager()
    session = manager.get_session(session_id)
    if session is None:
        return {"error": f"Session '{session_id}' not found"}
    if manager.is_session_running(session_id):
        return {"error": f"Cannot destroy session '{session_id}': a run is currently in progress"}
    success = manager.destroy_session(session_id, delete_profiles=delete_profiles)
    if success:
        return {"session_id": session_id, "destroyed": True}
    return {"error": f"Failed to destroy session '{session_id}'"}


@mcp.tool()
async def list_sessions() -> list[dict]:
    """List all profiling sessions.

    Returns:
        List of dicts with session_id, state, binary_path, run_count, and created_at.
    """
    manager = _get_manager()
    sessions = manager.list_sessions()
    return [
        {
            "session_id": s.id,
            "state": s.state.value,
            "binary_path": str(s.binary_path),
            "run_count": len(s.runs),
            "created_at": s.created_at.isoformat(),
        }
        for s in sessions
    ]


@mcp.tool()
async def run(
    session_id: str,
    timeout_s: float | None = None,
    output_mode: str = "STDOUT",
) -> dict:
    """Execute a profiling run for an approved session.

    Runs samply to profile the binary and captures the profile data.

    Args:
        session_id: The session ID to run.
        timeout_s: Optional timeout in seconds for the profiling run.
        output_mode: How to handle stdout from the binary. One of: STDOUT, DEVNULL, FILE.

    Returns:
        dict with run_id, duration_s, sample_count, status, exit_code, and log_path.
    """
    manager = _get_manager()
    session = manager.get_session(session_id)

    if session is None:
        return {"error": f"Session '{session_id}' not found"}

    if session.state.value != "approved":
        return {
            "error": (
                f"Session '{session_id}' is not approved (current state: {session.state.value})"
            ),
            "session_id": session_id,
            "state": session.state.value,
        }

    is_ok, level = check_perf_paranoid()
    if not is_ok:
        return {
            "error": f"kernel.perf_event_paranoid is {level} (must be <= 1 for profiling).\n"
            "Run: sudo sysctl -w kernel.perf_event_paranoid=1",
            "session_id": session_id,
        }

    try:
        output_mode_enum = OutputMode(output_mode)
    except ValueError:
        return {
            "error": f"Invalid output_mode '{output_mode}'. Must be one of: STDOUT, DEVNULL, FILE",
            "session_id": session_id,
        }

    await manager.acquire_run_lock(session_id)

    try:
        if session.setup_script_snapshot:
            setup_result = await execute_setup_script(
                script_content=session.setup_script_snapshot,
                working_directory=session.working_directory,
                env=session.env,
            )

            if not setup_result.success:
                return {
                    "run_id": 0,
                    "duration_s": 0.0,
                    "sample_count": 0,
                    "status": "failed",
                    "exit_code": setup_result.exit_code,
                    "stdout": setup_result.stdout[:1000] if setup_result.stdout else None,
                    "stderr": setup_result.stderr[:1000] if setup_result.stderr else "",
                    "log_path": None,
                    "error": f"Setup script failed with exit code {setup_result.exit_code}",
                }

        run_id = manager.get_next_run_id(session_id)
        profile_path = manager.get_profile_path(session_id, run_id)

        result = await run_samply(
            binary_path=session.binary_path,
            args=session.args,
            profile_path=profile_path,
            working_directory=session.working_directory,
            env=session.env,
            timeout_s=timeout_s,
            output_mode=output_mode_enum,
            session_id=session_id,
            run_id=run_id,
        )

        run_record = Run(
            id=run_id,
            started_at=datetime.now(),
            duration_s=result.duration_s,
            sample_count=result.sample_count,
            profile_path=profile_path,
            status=result.status,
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            log_path=result.log_path,
        )
        manager.add_run(session_id, run_record)

        return {
            "run_id": run_id,
            "duration_s": result.duration_s,
            "sample_count": result.sample_count,
            "status": result.status.value,
            "exit_code": result.exit_code,
            "stdout": result.stdout if output_mode_enum == OutputMode.STDOUT else None,
            "stderr": result.stderr,
            "log_path": str(result.log_path) if result.log_path else None,
            "error": result.error,
        }

    finally:
        await manager.release_run_lock()


def _get_run(session_id: str, run_id: int | None) -> tuple[Run | None, Path | None, dict | None]:
    manager = _get_manager()
    session = manager.get_session(session_id)
    if session is None:
        return None, None, {"error": f"Session '{session_id}' not found"}

    if not session.runs:
        return None, None, {"error": f"No runs found for session '{session_id}'"}

    if run_id is None:
        return session.runs[-1], session.binary_path, None

    for run in session.runs:
        if run.id == run_id:
            return run, session.binary_path, None

    return None, None, {"error": f"Run '{run_id}' not found in session '{session_id}'"}


def _load_profile(run: Run, binary_path: Path | None = None) -> GeckoProfile | dict:
    if not run.profile_path.exists():
        return {"error": f"Profile file not found: {run.profile_path}"}
    try:
        symbolizer = Symbolizer(binary_path) if binary_path else None
        return parse_gecko_profile(run.profile_path, symbolizer=symbolizer)
    except ParseError as e:
        return {"error": f"Failed to parse profile: {e}"}


@mcp.tool()
async def get_run_summary(session_id: str, run_id: int | None = None) -> dict:
    """Get a summary of a profiling run with top 5 hot functions.

    Args:
        session_id: The session ID.
        run_id: The run ID. If None, uses the latest run.

    Returns:
        dict with run_id, duration_s, sample_count, and top_functions
        (list with name, self_pct, total_pct).
    """
    run, binary_path, error = _get_run(session_id, run_id)
    if error:
        return error
    if run is None:
        return {"error": "Internal error: run is None"}

    profile_or_error = _load_profile(run, binary_path)
    if isinstance(profile_or_error, dict):
        return profile_or_error

    profile = profile_or_error
    top_funcs = profile.hot_functions(top_n=5)

    return {
        "run_id": run.id,
        "duration_s": round(run.duration_s, 3),
        "sample_count": run.sample_count,
        "top_functions": [
            {
                "name": f.name,
                "self_pct": f.self_pct,
                "total_pct": f.total_pct,
                "file": f.file,
                "line": f.line,
            }
            for f in top_funcs
        ],
    }


@mcp.tool()
async def get_hot_functions(
    session_id: str,
    top_n: int = 10,
    run_id: int | None = None,
) -> list[dict] | dict:
    """Get the hottest functions by self time from a profiling run.

    Args:
        session_id: The session ID.
        top_n: Number of top functions to return. Default 10.
        run_id: The run ID. If None, uses the latest run.

    Returns:
        List of dicts with rank, name, self_pct, total_pct, is_inlined, file, line.
    """
    run, binary_path, error = _get_run(session_id, run_id)
    if error:
        return error
    if run is None:
        return {"error": "Internal error: run is None"}

    profile_or_error = _load_profile(run, binary_path)
    if isinstance(profile_or_error, dict):
        return profile_or_error

    profile = profile_or_error
    hot_funcs = profile.hot_functions(top_n=top_n)

    return [
        {
            "rank": f.rank,
            "name": f.name,
            "self_pct": f.self_pct,
            "total_pct": f.total_pct,
            "is_inlined": f.is_inlined,
            "file": f.file,
            "line": f.line,
        }
        for f in hot_funcs
    ]


@mcp.tool()
async def get_callers(
    session_id: str,
    function_name: str,
    run_id: int | None = None,
) -> dict:
    """Get the callers of a function (who calls this function).

    Args:
        session_id: The session ID.
        function_name: Function name or fragment to search for (case-insensitive).
        run_id: The run ID. If None, uses the latest run.

    Returns:
        dict with matched_function (name, self_pct, total_pct, is_inlined),
        match_warning (if multiple matches), and callers list with name, self_pct,
        total_pct, is_inlined, and call_pct (% of calls from this caller).
    """
    run, binary_path, error = _get_run(session_id, run_id)
    if error:
        return error
    if run is None:
        return {"error": "Internal error: run is None"}

    profile_or_error = _load_profile(run, binary_path)
    if isinstance(profile_or_error, dict):
        return profile_or_error

    profile = profile_or_error
    try:
        result = profile.callers_of(function_name)
    except ValueError as e:
        return {"error": str(e)}
    except ParseError as e:
        return {"error": str(e)}

    return {
        "matched_function": {
            "name": result.matched_function.name,
            "self_pct": result.matched_function.self_pct,
            "total_pct": result.matched_function.total_pct,
            "is_inlined": result.matched_function.is_inlined,
            "file": result.matched_function.file,
            "line": result.matched_function.line,
        },
        "match_warning": result.match_warning,
        "callers": [
            {
                "name": c.name,
                "self_pct": c.self_pct,
                "total_pct": c.total_pct,
                "is_inlined": c.is_inlined,
                "file": c.file,
                "line": c.line,
                "call_pct": c.call_pct,
            }
            for c in result.callers
        ],
    }


@mcp.tool()
async def get_callees(
    session_id: str,
    function_name: str,
    run_id: int | None = None,
) -> dict:
    """Get the callees of a function (what this function calls).

    Args:
        session_id: The session ID.
        function_name: Function name or fragment to search for (case-insensitive).
        run_id: The run ID. If None, uses the latest run.

    Returns:
        dict with matched_function (name, self_pct, total_pct, is_inlined),
        match_warning (if multiple matches), and callees list with name, self_pct,
        total_pct, is_inlined, and call_pct (% of calls to this callee).
    """
    run, binary_path, error = _get_run(session_id, run_id)
    if error:
        return error
    if run is None:
        return {"error": "Internal error: run is None"}

    profile_or_error = _load_profile(run, binary_path)
    if isinstance(profile_or_error, dict):
        return profile_or_error

    profile = profile_or_error
    try:
        result = profile.callees_of(function_name)
    except ValueError as e:
        return {"error": str(e)}
    except ParseError as e:
        return {"error": str(e)}

    return {
        "matched_function": {
            "name": result.matched_function.name,
            "self_pct": result.matched_function.self_pct,
            "total_pct": result.matched_function.total_pct,
            "is_inlined": result.matched_function.is_inlined,
            "file": result.matched_function.file,
            "line": result.matched_function.line,
        },
        "match_warning": result.match_warning,
        "callees": [
            {
                "name": c.name,
                "self_pct": c.self_pct,
                "total_pct": c.total_pct,
                "is_inlined": c.is_inlined,
                "file": c.file,
                "line": c.line,
                "call_pct": c.call_pct,
            }
            for c in result.callees
        ],
    }


@mcp.tool()
async def compare_runs(
    session_id: str,
    run_id_a: int,
    run_id_b: int,
) -> dict:
    """Compare two profiling runs to identify performance changes.

    Args:
        session_id: The session ID.
        run_id_a: First run ID (baseline).
        run_id_b: Second run ID (comparison).

    Returns:
        dict with duration_delta_s, duration_delta_pct, sample_count_a/b,
        improved (functions that got faster), regressed (functions that got slower),
        new_hotspots, and resolved (functions that were hot but no longer are).
    """
    run_a, binary_path_a, error_a = _get_run(session_id, run_id_a)
    if error_a:
        return error_a
    if run_a is None:
        return {"error": "Internal error: run_a is None"}

    run_b, binary_path_b, error_b = _get_run(session_id, run_id_b)
    if error_b:
        return error_b
    if run_b is None:
        return {"error": "Internal error: run_b is None"}

    profile_a_or_error = _load_profile(run_a, binary_path_a)
    if isinstance(profile_a_or_error, dict):
        return profile_a_or_error

    profile_b_or_error = _load_profile(run_b, binary_path_b)
    if isinstance(profile_b_or_error, dict):
        return profile_b_or_error

    profile_a = profile_a_or_error
    profile_b = profile_b_or_error

    try:
        result = profile_a.compare(profile_b)
    except ParseError as e:
        return {"error": str(e)}

    return {
        "duration_delta_s": result.duration_delta_s,
        "duration_delta_pct": result.duration_delta_pct,
        "sample_count_a": result.sample_count_a,
        "sample_count_b": result.sample_count_b,
        "improved": result.improved,
        "regressed": result.regressed,
        "new_hotspots": result.new_hotspots,
        "resolved": result.resolved,
    }


def main() -> None:
    global _manager, _auto_approve
    parser = argparse.ArgumentParser(
        description="samply-mcp: MCP server for samply profiling",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=7837,
        help="Port to listen on (default: 7837)",
    )
    parser.add_argument(
        "--dangerously-auto-approve-sessions",
        action="store_true",
        help="Automatically approve all sessions without manual review (dangerous)",
    )

    args = parser.parse_args()

    validate_startup()

    _manager = SessionManager()
    _auto_approve = args.dangerously_auto_approve_sessions

    print(f"samply-mcp server starting on http://localhost:{args.port}", file=sys.stderr)
    if args.dangerously_auto_approve_sessions:
        print(
            "WARNING: Auto-approval mode enabled. All sessions will be automatically approved.",
            file=sys.stderr,
        )

    server_thread = threading.Thread(
        target=lambda: mcp.run(transport="http", host="127.0.0.1", port=args.port),
        daemon=True,
    )
    server_thread.start()

    repl = REPL(_manager)
    import asyncio

    asyncio.run(repl.run())


if __name__ == "__main__":
    main()
