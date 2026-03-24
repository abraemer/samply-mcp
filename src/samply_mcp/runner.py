"""samply execution and timeout handling."""

import asyncio
import json
import os
import signal
import time
from dataclasses import dataclass
from pathlib import Path

from samply_mcp.session import OutputMode, RunStatus

MAX_OUTPUT_LEN = 1000
PERF_PARANOID_PATH = "/proc/sys/kernel/perf_event_paranoid"


@dataclass
class SamplyResult:
    success: bool
    profile_path: Path | None
    sample_count: int
    duration_s: float
    stdout: str
    stderr: str
    samply_stdout: str
    samply_stderr: str
    exit_code: int
    status: RunStatus
    log_path: Path | None
    error: str | None = None


def _truncate_output(output: str) -> str:
    if len(output) <= MAX_OUTPUT_LEN:
        return output
    return f"{output[:MAX_OUTPUT_LEN]}... [truncated, {len(output)} bytes total]"


@dataclass
class SetupScriptResult:
    success: bool
    stdout: str
    stderr: str
    exit_code: int


async def execute_setup_script(
    script_content: str,
    working_directory: Path,
    env: dict | None = None,
) -> SetupScriptResult:
    """
    Execute a setup script from snapshotted content (not file path).

    - Shell: /bin/bash
    - Working directory: session's working_directory
    - Environment: server's environment merged with session's env (session env takes precedence)
    - No timeout (expected to complete quickly)
    - Execute from snapshotted content, not from file path

    Returns result with success, stdout, stderr, exit_code.
    """
    merged_env = os.environ.copy()
    if env is not None:
        merged_env.update(env)

    process = await asyncio.create_subprocess_exec(
        "/bin/bash",
        "-c",
        script_content,
        cwd=working_directory,
        env=merged_env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    stdout_bytes, stderr_bytes = await process.communicate()

    stdout = stdout_bytes.decode("utf-8", errors="replace")
    stderr = stderr_bytes.decode("utf-8", errors="replace")
    exit_code = process.returncode if process.returncode is not None else -1

    return SetupScriptResult(
        success=exit_code == 0,
        stdout=stdout,
        stderr=stderr,
        exit_code=exit_code,
    )


async def run_samply(
    binary_path: Path,
    args: list[str],
    profile_path: Path,
    working_directory: Path,
    env: dict | None = None,
    timeout_s: float | None = None,
    output_mode: OutputMode = OutputMode.STDOUT,
    session_id: str = "",
    run_id: int = 1,
    check_perf: bool = True,
) -> SamplyResult:
    """
    Execute samply to profile a binary.

    Command: samply record --output <profile_path> -- <binary_path> <args...>

    - samply attaches to spawned process and samples until exit
    - Binary stdout/stderr routed per output_mode
    - samply's own output captured separately
    - Profile written to profile_path

    Preconditions checked:
    - perf_event_paranoid <= 1 (if check_perf=True)
    - Binary exists (defensive check)
    """
    if check_perf:
        ok, level, error_msg = check_perf_paranoid()
        if not ok:
            return SamplyResult(
                success=False,
                profile_path=None,
                sample_count=0,
                duration_s=0.0,
                stdout="",
                stderr="",
                samply_stdout="",
                samply_stderr="",
                exit_code=-1,
                status=RunStatus.failed,
                log_path=None,
                error=error_msg,
            )

    if not binary_path.exists():
        return SamplyResult(
            success=False,
            profile_path=None,
            sample_count=0,
            duration_s=0.0,
            stdout="",
            stderr="",
            samply_stdout="",
            samply_stderr="",
            exit_code=-1,
            status=RunStatus.failed,
            log_path=None,
            error=f"Binary not found: {binary_path}",
        )

    merged_env = os.environ.copy()
    if env is not None:
        merged_env.update(env)

    profile_path.parent.mkdir(parents=True, exist_ok=True)

    if output_mode == OutputMode.FILE:
        log_dir = working_directory / ".profiling-logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"session-{session_id}-run-{run_id}.log"
    else:
        log_path = None

    cmd_args = [
        "record",
        "--output",
        str(profile_path),
        "--save-only",
        "--",
        str(binary_path),
    ] + args

    process = await asyncio.create_subprocess_exec(
        "samply",
        *cmd_args,
        cwd=working_directory,
        env=merged_env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        stdin=asyncio.subprocess.DEVNULL,
        start_new_session=True,
    )

    start_time = time.monotonic()

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(), timeout=timeout_s
        )
        status = RunStatus.success if process.returncode == 0 else RunStatus.failed
        exit_code = process.returncode if process.returncode is not None else -1
    except TimeoutError:
        if process.pid is not None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        stdout_bytes, stderr_bytes = await process.communicate()
        status = RunStatus.timeout
        exit_code = -1

    duration_s = time.monotonic() - start_time

    samply_stdout = stdout_bytes.decode("utf-8", errors="replace")
    samply_stderr = stderr_bytes.decode("utf-8", errors="replace")

    lines = samply_stdout.split("\n")
    binary_lines = []
    samply_lines = []
    for line in lines:
        if line.startswith("[samply]"):
            samply_lines.append(line)
        else:
            binary_lines.append(line)

    binary_stdout = "\n".join(binary_lines)
    samply_output = "\n".join(samply_lines)

    if output_mode == OutputMode.FILE and log_path is not None:
        log_path.write_text(binary_stdout)
        result_stdout = binary_stdout
        result_stderr = ""
    elif output_mode == OutputMode.STDOUT:
        result_stdout = _truncate_output(binary_stdout)
        result_stderr = _truncate_output(samply_stderr)
    else:
        result_stdout = ""
        result_stderr = ""

    sample_count = 0
    if profile_path.exists():
        try:
            with profile_path.open() as f:
                profile_data = json.load(f)
            for thread in profile_data.get("threads", []):
                sample_count += len(thread.get("samples", {}).get("data", []))
        except (json.JSONDecodeError, KeyError):
            pass

    success = status == RunStatus.success and profile_path.exists()

    error = None
    if status == RunStatus.failed:
        error_parts = []
        if samply_output:
            error_parts.append(samply_output)
        if samply_stderr:
            error_parts.append(samply_stderr)
        if error_parts:
            error = "samply failed: " + "\n".join(error_parts)

    return SamplyResult(
        success=success,
        profile_path=profile_path if profile_path.exists() else None,
        sample_count=sample_count,
        duration_s=duration_s,
        stdout=result_stdout,
        stderr=result_stderr,
        samply_stdout=samply_output,
        samply_stderr=samply_stderr,
        exit_code=exit_code,
        status=status,
        log_path=log_path,
        error=error,
    )


def get_perf_paranoid_level() -> int:
    """Read /proc/sys/kernel/perf_event_paranoid and return value.

    Returns -1 if the file cannot be read or parsed.
    """
    try:
        content = Path(PERF_PARANOID_PATH).read_text().strip()
        return int(content)
    except (OSError, ValueError):
        return -1


def check_perf_paranoid() -> tuple[bool, int, str | None]:
    """Check if perf_event_paranoid <= 1.

    Returns:
        (ok, level, error_message)
        - ok: True if profiling is allowed
        - level: the actual value, or -1 if unreadable
        - error_message: None if ok, otherwise instructions
    """
    level = get_perf_paranoid_level()

    if level < 0:
        return (False, level, "Cannot read kernel.perf_event_paranoid. Is this Linux?")

    if level > 1:
        return (
            False,
            level,
            f"kernel.perf_event_paranoid is {level} (must be <= 1 for profiling).\n"
            "Run: sudo sysctl -w kernel.perf_event_paranoid=1",
        )

    return (True, level, None)
