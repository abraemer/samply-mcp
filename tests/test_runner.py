"""Tests for runner module."""

import asyncio
import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from samply_mcp.runner import (
    MAX_OUTPUT_LEN,
    SamplyResult,
    SetupScriptResult,
    _truncate_output,
    check_perf_paranoid,
    execute_setup_script,
    get_perf_paranoid_level,
    run_samply,
)
from samply_mcp.session import OutputMode, RunStatus

SAMPLY_AVAILABLE = shutil.which("samply") is not None


def test_setup_script_executes_with_correct_environment():
    async def run_test():
        with tempfile.TemporaryDirectory() as tmpdir:
            script = 'echo "TEST_VAR=$TEST_VAR"'
            env = {"TEST_VAR": "hello_world"}

            result = await execute_setup_script(script, Path(tmpdir), env)

            assert result.success
            assert "TEST_VAR=hello_world" in result.stdout

    asyncio.run(run_test())


def test_setup_script_non_zero_exit_returns_error():
    async def run_test():
        with tempfile.TemporaryDirectory() as tmpdir:
            script = 'echo "stdout content"; echo "stderr content" >&2; exit 42'

            result = await execute_setup_script(script, Path(tmpdir), None)

            assert not result.success
            assert result.exit_code == 42
            assert "stdout content" in result.stdout
            assert "stderr content" in result.stderr

    asyncio.run(run_test())


def test_setup_script_accesses_merged_environment():
    async def run_test():
        with tempfile.TemporaryDirectory() as tmpdir:
            script = 'echo "PATH_EXISTS=${PATH:+yes}"; echo "CUSTOM_VAR=$CUSTOM_VAR"'
            env = {"CUSTOM_VAR": "custom_value"}

            result = await execute_setup_script(script, Path(tmpdir), env)

            assert result.success
            assert "PATH_EXISTS=yes" in result.stdout
            assert "CUSTOM_VAR=custom_value" in result.stdout

    asyncio.run(run_test())


def test_setup_script_runs_in_correct_working_directory():
    async def run_test():
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir) / "subdir"
            work_dir.mkdir()
            script = "pwd"

            result = await execute_setup_script(script, work_dir, None)

            assert result.success
            assert str(work_dir) in result.stdout

    asyncio.run(run_test())


def test_setup_script_session_env_takes_precedence():
    async def run_test():
        with tempfile.TemporaryDirectory() as tmpdir:
            os.environ["PRECEDENCE_TEST"] = "original"
            script = 'echo "PRECEDENCE_TEST=$PRECEDENCE_TEST"'
            env = {"PRECEDENCE_TEST": "overridden"}

            try:
                result = await execute_setup_script(script, Path(tmpdir), env)

                assert result.success
                assert "PRECEDENCE_TEST=overridden" in result.stdout
            finally:
                del os.environ["PRECEDENCE_TEST"]

    asyncio.run(run_test())


def test_setup_script_result_dataclass():
    result = SetupScriptResult(success=True, stdout="out", stderr="err", exit_code=0)

    assert result.success is True
    assert result.stdout == "out"
    assert result.stderr == "err"
    assert result.exit_code == 0


def test_setup_script_handles_special_characters():
    async def run_test():
        with tempfile.TemporaryDirectory() as tmpdir:
            script = 'echo "hello world"'

            result = await execute_setup_script(script, Path(tmpdir), None)

            assert result.success
            assert "hello world" in result.stdout

    asyncio.run(run_test())


def test_truncate_output_short():
    output = "short output"
    assert _truncate_output(output) == output


def test_truncate_output_exact_boundary():
    output = "x" * MAX_OUTPUT_LEN
    assert _truncate_output(output) == output
    assert len(_truncate_output(output)) == MAX_OUTPUT_LEN


def test_truncate_output_over_boundary():
    output = "x" * (MAX_OUTPUT_LEN + 100)
    truncated = _truncate_output(output)
    assert truncated.startswith("x" * MAX_OUTPUT_LEN)
    assert "... [truncated," in truncated
    assert f"{len(output)} bytes total]" in truncated


@pytest.mark.skipif(not SAMPLY_AVAILABLE, reason="samply not installed")
def test_samply_command_construction():
    async def run_test():
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_path = Path(tmpdir) / "profiles" / "test.json"
            working_dir = Path(tmpdir)

            result = await run_samply(
                binary_path=Path("/bin/true"),
                args=[],
                profile_path=profile_path,
                working_directory=working_dir,
                output_mode=OutputMode.STDOUT,
            )

            assert result.success
            assert result.status == RunStatus.success
            assert result.exit_code == 0

    asyncio.run(run_test())


@pytest.mark.skipif(not SAMPLY_AVAILABLE, reason="samply not installed")
def test_samply_output_mode_stdout():
    async def run_test():
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_path = Path(tmpdir) / "profiles" / "test.json"
            working_dir = Path(tmpdir)

            result = await run_samply(
                binary_path=Path("/bin/echo"),
                args=["hello", "world"],
                profile_path=profile_path,
                working_directory=working_dir,
                output_mode=OutputMode.STDOUT,
            )

            assert result.success
            assert "hello world" in result.stdout

    asyncio.run(run_test())


@pytest.mark.skipif(not SAMPLY_AVAILABLE, reason="samply not installed")
def test_samply_output_mode_devnull():
    async def run_test():
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_path = Path(tmpdir) / "profiles" / "test.json"
            working_dir = Path(tmpdir)

            result = await run_samply(
                binary_path=Path("/bin/echo"),
                args=["hello", "world"],
                profile_path=profile_path,
                working_directory=working_dir,
                output_mode=OutputMode.DEVNULL,
            )

            assert result.success
            assert result.stdout == ""

    asyncio.run(run_test())


@pytest.mark.skipif(not SAMPLY_AVAILABLE, reason="samply not installed")
def test_samply_output_mode_file():
    async def run_test():
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_path = Path(tmpdir) / "profiles" / "test.json"
            working_dir = Path(tmpdir)

            result = await run_samply(
                binary_path=Path("/bin/echo"),
                args=["hello", "world"],
                profile_path=profile_path,
                working_directory=working_dir,
                output_mode=OutputMode.FILE,
                session_id="test12",
                run_id=1,
            )

            assert result.success
            assert result.log_path is not None
            assert result.log_path.exists()
            log_content = result.log_path.read_text()
            assert "hello world" in log_content

    asyncio.run(run_test())


@pytest.mark.skipif(not SAMPLY_AVAILABLE, reason="samply not installed")
def test_samply_profile_written_to_expected_path():
    async def run_test():
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_path = Path(tmpdir) / "nested" / "dir" / "profile.json"
            working_dir = Path(tmpdir)

            result = await run_samply(
                binary_path=Path("/bin/true"),
                args=[],
                profile_path=profile_path,
                working_directory=working_dir,
                output_mode=OutputMode.STDOUT,
            )

            assert result.success
            assert result.profile_path is not None
            assert result.profile_path == profile_path
            assert profile_path.exists()

    asyncio.run(run_test())


@pytest.mark.skipif(not SAMPLY_AVAILABLE, reason="samply not installed")
def test_samply_truncation_at_1000_chars():
    async def run_test():
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_path = Path(tmpdir) / "profiles" / "test.json"
            working_dir = Path(tmpdir)

            long_output = "x" * 2000
            result = await run_samply(
                binary_path=Path("/bin/echo"),
                args=[long_output],
                profile_path=profile_path,
                working_directory=working_dir,
                output_mode=OutputMode.STDOUT,
            )

            assert result.success
            assert "... [truncated," in result.stdout
            assert "2000 bytes total]" in result.stdout or "2001 bytes total]" in result.stdout

    asyncio.run(run_test())


@pytest.mark.skipif(not SAMPLY_AVAILABLE, reason="samply not installed")
def test_samply_timeout():
    async def run_test():
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_path = Path(tmpdir) / "profiles" / "test.json"
            working_dir = Path(tmpdir)

            result = await run_samply(
                binary_path=Path("/bin/sleep"),
                args=["10"],
                profile_path=profile_path,
                working_directory=working_dir,
                timeout_s=0.5,
                output_mode=OutputMode.STDOUT,
            )

            assert result.status == RunStatus.timeout
            assert result.exit_code == -1
            assert result.duration_s < 5

    asyncio.run(run_test())


@pytest.mark.skipif(not SAMPLY_AVAILABLE, reason="samply not installed")
def test_samply_timeout_captures_partial_profile():
    async def run_test():
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_path = Path(tmpdir) / "profiles" / "test.json"
            working_dir = Path(tmpdir)

            result = await run_samply(
                binary_path=Path("/bin/bash"),
                args=["-c", "echo 'starting'; sleep 0.2; echo 'middle'; sleep 10"],
                profile_path=profile_path,
                working_directory=working_dir,
                timeout_s=1.0,
                output_mode=OutputMode.STDOUT,
            )

            assert result.status == RunStatus.timeout
            assert result.exit_code == -1
            assert result.duration_s >= 0.5
            assert result.duration_s < 5

    asyncio.run(run_test())


@pytest.mark.skipif(not SAMPLY_AVAILABLE, reason="samply not installed")
def test_samply_timeout_none_allows_unlimited_runtime():
    async def run_test():
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_path = Path(tmpdir) / "profiles" / "test.json"
            working_dir = Path(tmpdir)

            result = await run_samply(
                binary_path=Path("/bin/sleep"),
                args=["0.5"],
                profile_path=profile_path,
                working_directory=working_dir,
                timeout_s=None,
                output_mode=OutputMode.STDOUT,
            )

            assert result.success
            assert result.status == RunStatus.success
            assert result.exit_code == 0
            assert result.duration_s >= 0.3

    asyncio.run(run_test())


@pytest.mark.skipif(not SAMPLY_AVAILABLE, reason="samply not installed")
def test_samply_samply_output_captured_separately():
    async def run_test():
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_path = Path(tmpdir) / "profiles" / "test.json"
            working_dir = Path(tmpdir)

            result = await run_samply(
                binary_path=Path("/bin/echo"),
                args=["binary_output"],
                profile_path=profile_path,
                working_directory=working_dir,
                output_mode=OutputMode.STDOUT,
            )

            assert result.success
            assert "binary_output" in result.stdout
            assert isinstance(result.samply_stdout, str)
            assert isinstance(result.samply_stderr, str)

    asyncio.run(run_test())


def test_samply_result_dataclass():
    result = SamplyResult(
        success=True,
        profile_path=Path("/tmp/test.json"),
        sample_count=100,
        duration_s=1.5,
        stdout="out",
        stderr="err",
        samply_stdout="samply out",
        samply_stderr="samply err",
        exit_code=0,
        status=RunStatus.success,
        log_path=None,
    )

    assert result.success is True
    assert result.sample_count == 100
    assert result.duration_s == 1.5
    assert result.status == RunStatus.success


def test_get_perf_paranoid_level():
    level = get_perf_paranoid_level()
    assert isinstance(level, int)
    assert level >= -1


def test_check_perf_paranoid_ok():
    with patch("samply_mcp.runner.get_perf_paranoid_level", return_value=1):
        ok, level, error = check_perf_paranoid()
        assert ok is True
        assert level == 1
        assert error is None


def test_check_perf_paranoid_too_high():
    with patch("samply_mcp.runner.get_perf_paranoid_level", return_value=2):
        ok, level, error = check_perf_paranoid()
        assert ok is False
        assert level == 2
        assert error is not None
        assert "kernel.perf_event_paranoid is 2" in error
        assert "sudo sysctl" in error


def test_check_perf_paranoid_unreadable():
    with patch("samply_mcp.runner.get_perf_paranoid_level", return_value=-1):
        ok, level, error = check_perf_paranoid()
        assert ok is False
        assert level == -1
        assert error is not None
        assert "Cannot read" in error


@pytest.mark.skipif(not SAMPLY_AVAILABLE, reason="samply not installed")
def test_samply_binary_not_found():
    async def run_test():
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_path = Path(tmpdir) / "profiles" / "test.json"
            working_dir = Path(tmpdir)

            result = await run_samply(
                binary_path=Path("/nonexistent/binary"),
                args=[],
                profile_path=profile_path,
                working_directory=working_dir,
                output_mode=OutputMode.STDOUT,
                check_perf=False,
            )

            assert not result.success
            assert result.error is not None
            assert "Binary not found" in result.error

    asyncio.run(run_test())


@pytest.mark.skipif(not SAMPLY_AVAILABLE, reason="samply not installed")
def test_samply_perf_paranoid_too_high():
    async def run_test():
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_path = Path(tmpdir) / "profiles" / "test.json"
            working_dir = Path(tmpdir)

            with patch(
                "samply_mcp.runner.check_perf_paranoid", return_value=(False, 2, "perf error")
            ):
                result = await run_samply(
                    binary_path=Path("/bin/true"),
                    args=[],
                    profile_path=profile_path,
                    working_directory=working_dir,
                    output_mode=OutputMode.STDOUT,
                    check_perf=True,
                )

            assert not result.success
            assert result.error == "perf error"

    asyncio.run(run_test())


@pytest.mark.skipif(not SAMPLY_AVAILABLE, reason="samply not installed")
def test_samply_binary_exits_non_zero():
    async def run_test():
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_path = Path(tmpdir) / "profiles" / "test.json"
            working_dir = Path(tmpdir)

            result = await run_samply(
                binary_path=Path("/bin/bash"),
                args=["-c", "exit 42"],
                profile_path=profile_path,
                working_directory=working_dir,
                output_mode=OutputMode.STDOUT,
            )

            assert result.success
            assert result.status == RunStatus.success

    asyncio.run(run_test())


def test_samply_result_error_field():
    result = SamplyResult(
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
        error="Binary not found: /nonexistent",
    )

    assert not result.success
    assert result.error == "Binary not found: /nonexistent"
