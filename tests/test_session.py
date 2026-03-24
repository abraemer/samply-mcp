"""Tests for session dataclasses."""

from datetime import datetime
from pathlib import Path

from samply_mcp.session import (
    OutputMode,
    Run,
    RunStatus,
    Session,
    SessionState,
    compute_command_hash,
)


def test_session_state_enum():
    assert SessionState.pending_approval.value == "pending_approval"
    assert SessionState.approved.value == "approved"
    assert SessionState.error.value == "error"


def test_run_status_enum():
    assert RunStatus.success.value == "success"
    assert RunStatus.failed.value == "failed"
    assert RunStatus.timeout.value == "timeout"


def test_output_mode_enum():
    assert OutputMode.STDOUT.value == "STDOUT"
    assert OutputMode.DEVNULL.value == "DEVNULL"
    assert OutputMode.FILE.value == "FILE"


def test_run_dataclass_instantiation():
    now = datetime.now()
    run = Run(
        id=1,
        started_at=now,
        duration_s=1.5,
        sample_count=100,
        profile_path=Path("/tmp/profile.json"),
        status=RunStatus.success,
        exit_code=0,
        stdout="output",
        stderr="",
        log_path=None,
    )
    assert run.id == 1
    assert run.started_at == now
    assert run.duration_s == 1.5
    assert run.sample_count == 100
    assert run.profile_path == Path("/tmp/profile.json")
    assert run.status == RunStatus.success
    assert run.exit_code == 0
    assert run.stdout == "output"
    assert run.stderr == ""
    assert run.log_path is None


def test_run_dataclass_with_log_path():
    run = Run(
        id=2,
        started_at=datetime.now(),
        duration_s=2.0,
        sample_count=200,
        profile_path=Path("/tmp/profile2.json"),
        status=RunStatus.timeout,
        exit_code=-1,
        stdout="",
        stderr="timeout error",
        log_path=Path("/tmp/run.log"),
    )
    assert run.log_path == Path("/tmp/run.log")
    assert run.status == RunStatus.timeout
    assert run.exit_code == -1


def test_session_dataclass_instantiation():
    now = datetime.now()
    session = Session(
        id="abc123",
        binary_path=Path("/usr/bin/true"),
        args=["--help"],
        setup_script_path=None,
        setup_script_snapshot=None,
        env={"FOO": "bar"},
        working_directory=Path("/tmp"),
        state=SessionState.pending_approval,
        runs=[],
        created_at=now,
    )
    assert session.id == "abc123"
    assert session.binary_path == Path("/usr/bin/true")
    assert session.args == ["--help"]
    assert session.setup_script_path is None
    assert session.setup_script_snapshot is None
    assert session.env == {"FOO": "bar"}
    assert session.working_directory == Path("/tmp")
    assert session.state == SessionState.pending_approval
    assert session.runs == []
    assert session.created_at == now


def test_session_default_values():
    session = Session(
        id="xyz789",
        binary_path=Path("/bin/ls"),
        args=[],
        setup_script_path=None,
        setup_script_snapshot=None,
        env=None,
        working_directory=Path("/home"),
        state=SessionState.approved,
    )
    assert session.runs == []
    assert isinstance(session.created_at, datetime)


def test_command_hash_consistent():
    hash1 = compute_command_hash(
        binary_path=Path("/usr/bin/true"),
        args=["--help"],
        setup_script_snapshot="echo hello",
        env={"FOO": "bar"},
        working_directory=Path("/tmp"),
    )
    hash2 = compute_command_hash(
        binary_path=Path("/usr/bin/true"),
        args=["--help"],
        setup_script_snapshot="echo hello",
        env={"FOO": "bar"},
        working_directory=Path("/tmp"),
    )
    assert hash1 == hash2
    assert len(hash1) == 64


def test_command_hash_different_binary():
    hash1 = compute_command_hash(
        binary_path=Path("/usr/bin/true"),
        args=["--help"],
        setup_script_snapshot=None,
        env=None,
        working_directory=Path("/tmp"),
    )
    hash2 = compute_command_hash(
        binary_path=Path("/usr/bin/false"),
        args=["--help"],
        setup_script_snapshot=None,
        env=None,
        working_directory=Path("/tmp"),
    )
    assert hash1 != hash2


def test_command_hash_different_args():
    hash1 = compute_command_hash(
        binary_path=Path("/usr/bin/true"),
        args=["--help"],
        setup_script_snapshot=None,
        env=None,
        working_directory=Path("/tmp"),
    )
    hash2 = compute_command_hash(
        binary_path=Path("/usr/bin/true"),
        args=["--version"],
        setup_script_snapshot=None,
        env=None,
        working_directory=Path("/tmp"),
    )
    assert hash1 != hash2


def test_command_hash_different_setup_script():
    hash1 = compute_command_hash(
        binary_path=Path("/usr/bin/true"),
        args=[],
        setup_script_snapshot="echo hello",
        env=None,
        working_directory=Path("/tmp"),
    )
    hash2 = compute_command_hash(
        binary_path=Path("/usr/bin/true"),
        args=[],
        setup_script_snapshot="echo world",
        env=None,
        working_directory=Path("/tmp"),
    )
    assert hash1 != hash2


def test_command_hash_different_env():
    hash1 = compute_command_hash(
        binary_path=Path("/usr/bin/true"),
        args=[],
        setup_script_snapshot=None,
        env={"FOO": "bar"},
        working_directory=Path("/tmp"),
    )
    hash2 = compute_command_hash(
        binary_path=Path("/usr/bin/true"),
        args=[],
        setup_script_snapshot=None,
        env={"FOO": "baz"},
        working_directory=Path("/tmp"),
    )
    assert hash1 != hash2


def test_command_hash_different_working_directory():
    hash1 = compute_command_hash(
        binary_path=Path("/usr/bin/true"),
        args=[],
        setup_script_snapshot=None,
        env=None,
        working_directory=Path("/tmp"),
    )
    hash2 = compute_command_hash(
        binary_path=Path("/usr/bin/true"),
        args=[],
        setup_script_snapshot=None,
        env=None,
        working_directory=Path("/home"),
    )
    assert hash1 != hash2


def test_command_hash_env_order_independent():
    hash1 = compute_command_hash(
        binary_path=Path("/usr/bin/true"),
        args=[],
        setup_script_snapshot=None,
        env={"A": "1", "B": "2"},
        working_directory=Path("/tmp"),
    )
    hash2 = compute_command_hash(
        binary_path=Path("/usr/bin/true"),
        args=[],
        setup_script_snapshot=None,
        env={"B": "2", "A": "1"},
        working_directory=Path("/tmp"),
    )
    assert hash1 == hash2


def test_session_command_hash_property():
    session = Session(
        id="test",
        binary_path=Path("/usr/bin/true"),
        args=["--help"],
        setup_script_path=None,
        setup_script_snapshot="echo test",
        env={"KEY": "value"},
        working_directory=Path("/tmp"),
        state=SessionState.pending_approval,
    )
    expected = compute_command_hash(
        Path("/usr/bin/true"),
        ["--help"],
        "echo test",
        {"KEY": "value"},
        Path("/tmp"),
    )
    assert session.command_hash == expected
