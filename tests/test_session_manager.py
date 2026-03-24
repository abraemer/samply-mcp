"""Tests for session manager."""

import asyncio
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from samply_mcp.session import Run, RunStatus, Session, SessionState
from samply_mcp.session_manager import SessionManager


def test_sessions_persist_across_instances():
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
        created_at=datetime(2024, 1, 1, 12, 0, 0),
    )

    db_path = Path("/tmp/test_samply_mcp.db")
    if db_path.exists():
        db_path.unlink()

    manager_a = SessionManager(db_path=db_path)
    manager_a._save_session(session)
    manager_a._sessions[session.id] = session

    manager_b = SessionManager(db_path=db_path)
    assert "abc123" in manager_b._sessions
    loaded = manager_b._sessions["abc123"]
    assert loaded.id == "abc123"
    assert loaded.binary_path == Path("/usr/bin/true")
    assert loaded.args == ["--help"]
    assert loaded.env == {"FOO": "bar"}
    assert loaded.state == SessionState.pending_approval

    db_path.unlink()


def test_runs_correctly_associated_with_sessions():
    session = Session(
        id="xyz789",
        binary_path=Path("/bin/ls"),
        args=[],
        setup_script_path=None,
        setup_script_snapshot=None,
        env=None,
        working_directory=Path("/tmp"),
        state=SessionState.approved,
        runs=[],
        created_at=datetime(2024, 1, 1, 12, 0, 0),
    )

    run = Run(
        id=1,
        started_at=datetime(2024, 1, 1, 12, 1, 0),
        duration_s=1.5,
        sample_count=100,
        profile_path=Path("/tmp/profile.json"),
        status=RunStatus.success,
        exit_code=0,
        stdout="output",
        stderr="",
        log_path=None,
    )

    manager = SessionManager(db_path=Path(":memory:"))
    manager._save_session(session)
    manager._save_run(session.id, run)

    manager2 = SessionManager(db_path=Path(":memory:"))
    manager2._sessions[session.id] = session
    session.runs.append(run)

    db_path = Path("/tmp/test_samply_mcp_runs.db")
    if db_path.exists():
        db_path.unlink()

    manager_a = SessionManager(db_path=db_path)
    manager_a._save_session(session)
    manager_a._save_run(session.id, run)
    manager_a._sessions[session.id] = session

    manager_b = SessionManager(db_path=db_path)
    assert "xyz789" in manager_b._sessions
    loaded = manager_b._sessions["xyz789"]
    assert len(loaded.runs) == 1
    assert loaded.runs[0].id == 1
    assert loaded.runs[0].duration_s == 1.5
    assert loaded.runs[0].sample_count == 100
    assert loaded.runs[0].status == RunStatus.success

    db_path.unlink()


def test_rehydration_works_with_empty_database():
    manager = SessionManager(db_path=Path(":memory:"))
    assert len(manager._sessions) == 0


def test_rehydration_works_with_existing_data():
    session1 = Session(
        id="sess1",
        binary_path=Path("/bin/true"),
        args=["-a"],
        setup_script_path=None,
        setup_script_snapshot=None,
        env=None,
        working_directory=Path("/tmp"),
        state=SessionState.approved,
        runs=[],
        created_at=datetime(2024, 1, 1, 10, 0, 0),
    )

    session2 = Session(
        id="sess2",
        binary_path=Path("/bin/ls"),
        args=["-l"],
        setup_script_path=Path("/tmp/setup.sh"),
        setup_script_snapshot="#!/bin/bash\necho setup",
        env={"DEBUG": "1"},
        working_directory=Path("/home"),
        state=SessionState.error,
        runs=[],
        created_at=datetime(2024, 1, 2, 10, 0, 0),
    )

    run1 = Run(
        id=1,
        started_at=datetime(2024, 1, 1, 10, 1, 0),
        duration_s=2.5,
        sample_count=500,
        profile_path=Path("/tmp/sess1/1.json"),
        status=RunStatus.success,
        exit_code=0,
        stdout="output1",
        stderr="",
        log_path=None,
    )

    run2 = Run(
        id=2,
        started_at=datetime(2024, 1, 1, 10, 2, 0),
        duration_s=3.0,
        sample_count=600,
        profile_path=Path("/tmp/sess1/2.json"),
        status=RunStatus.failed,
        exit_code=1,
        stdout="",
        stderr="error",
        log_path=Path("/tmp/sess1.log"),
    )

    run3 = Run(
        id=1,
        started_at=datetime(2024, 1, 2, 10, 1, 0),
        duration_s=0.5,
        sample_count=100,
        profile_path=Path("/tmp/sess2/1.json"),
        status=RunStatus.timeout,
        exit_code=-1,
        stdout="",
        stderr="timeout",
        log_path=None,
    )

    db_path = Path("/tmp/test_samply_mcp_restart.db")
    if db_path.exists():
        db_path.unlink()

    manager_a = SessionManager(db_path=db_path)
    manager_a._save_session(session1)
    manager_a._save_session(session2)
    manager_a._save_run("sess1", run1)
    manager_a._save_run("sess1", run2)
    manager_a._save_run("sess2", run3)
    manager_a._sessions[session1.id] = session1
    manager_a._sessions[session2.id] = session2
    session1.runs.extend([run1, run2])
    session2.runs.append(run3)

    manager_b = SessionManager(db_path=db_path)

    assert len(manager_b._sessions) == 2

    loaded1 = manager_b._sessions["sess1"]
    assert loaded1.binary_path == Path("/bin/true")
    assert loaded1.args == ["-a"]
    assert loaded1.state == SessionState.approved
    assert len(loaded1.runs) == 2
    assert loaded1.runs[0].id == 1
    assert loaded1.runs[0].duration_s == 2.5
    assert loaded1.runs[1].id == 2
    assert loaded1.runs[1].status == RunStatus.failed
    assert loaded1.runs[1].exit_code == 1
    assert loaded1.runs[1].log_path == Path("/tmp/sess1.log")

    loaded2 = manager_b._sessions["sess2"]
    assert loaded2.binary_path == Path("/bin/ls")
    assert loaded2.setup_script_path == Path("/tmp/setup.sh")
    assert loaded2.setup_script_snapshot == "#!/bin/bash\necho setup"
    assert loaded2.env == {"DEBUG": "1"}
    assert loaded2.state == SessionState.error
    assert len(loaded2.runs) == 1
    assert loaded2.runs[0].status == RunStatus.timeout
    assert loaded2.runs[0].exit_code == -1

    db_path.unlink()


def test_delete_session_removes_session_and_runs():
    session = Session(
        id="del1",
        binary_path=Path("/bin/true"),
        args=[],
        setup_script_path=None,
        setup_script_snapshot=None,
        env=None,
        working_directory=Path("/tmp"),
        state=SessionState.approved,
        runs=[],
        created_at=datetime(2024, 1, 1, 12, 0, 0),
    )

    run = Run(
        id=1,
        started_at=datetime(2024, 1, 1, 12, 1, 0),
        duration_s=1.0,
        sample_count=50,
        profile_path=Path("/tmp/del1/1.json"),
        status=RunStatus.success,
        exit_code=0,
        stdout="",
        stderr="",
        log_path=None,
    )

    db_path = Path("/tmp/test_samply_mcp_delete.db")
    if db_path.exists():
        db_path.unlink()

    manager_a = SessionManager(db_path=db_path)
    manager_a._save_session(session)
    manager_a._save_run("del1", run)
    manager_a._sessions[session.id] = session
    session.runs.append(run)

    manager_b = SessionManager(db_path=db_path)
    assert "del1" in manager_b._sessions
    assert len(manager_b._sessions["del1"].runs) == 1

    manager_b._delete_session("del1")
    assert "del1" not in manager_b._sessions

    manager_c = SessionManager(db_path=db_path)
    assert "del1" not in manager_c._sessions

    db_path.unlink()


def test_in_memory_database():
    manager = SessionManager(db_path=Path(":memory:"))
    assert len(manager._sessions) == 0

    session = Session(
        id="mem1",
        binary_path=Path("/bin/true"),
        args=[],
        setup_script_path=None,
        setup_script_snapshot=None,
        env=None,
        working_directory=Path("/tmp"),
        state=SessionState.pending_approval,
        runs=[],
        created_at=datetime(2024, 1, 1, 12, 0, 0),
    )
    manager._save_session(session)
    manager._sessions[session.id] = session

    assert "mem1" in manager._sessions


def test_create_session_basic():
    manager = SessionManager(db_path=Path(":memory:"))
    session = manager.create_session(
        binary_path=Path("/bin/true"),
        args=["--help"],
        working_directory=Path("/tmp"),
    )

    assert session.id is not None
    assert len(session.id) == 6
    assert session.binary_path == Path("/bin/true")
    assert session.args == ["--help"]
    assert session.state == SessionState.pending_approval
    assert session.setup_script_path is None
    assert session.setup_script_snapshot is None
    assert session.env is None
    assert session.working_directory == Path("/tmp")


def test_create_session_with_all_params():
    manager = SessionManager(db_path=Path(":memory:"))

    with tempfile.TemporaryDirectory() as tmpdir:
        setup_script = Path(tmpdir) / "setup.sh"
        setup_script.write_text("#!/bin/bash\necho setup")

        session = manager.create_session(
            binary_path=Path("/bin/ls"),
            args=["-la", "/tmp"],
            setup_script_path=setup_script,
            env={"FOO": "bar", "BAZ": "qux"},
            working_directory=Path(tmpdir),
        )

        assert session.binary_path == Path("/bin/ls")
        assert session.args == ["-la", "/tmp"]
        assert session.setup_script_path == setup_script
        assert session.setup_script_snapshot == "#!/bin/bash\necho setup"
        assert session.env == {"FOO": "bar", "BAZ": "qux"}
        assert session.working_directory == Path(tmpdir)
        assert session.state == SessionState.pending_approval


def test_create_session_auto_approve():
    manager = SessionManager(db_path=Path(":memory:"))
    session = manager.create_session(
        binary_path=Path("/bin/true"),
        args=[],
        working_directory=Path("/tmp"),
        auto_approve=True,
    )

    assert session.state == SessionState.approved


def test_create_session_persists():
    db_path = Path("/tmp/test_create_session.db")
    if db_path.exists():
        db_path.unlink()

    manager_a = SessionManager(db_path=db_path)
    session = manager_a.create_session(
        binary_path=Path("/bin/true"),
        args=["--test"],
        env={"KEY": "value"},
        working_directory=Path("/tmp"),
    )

    manager_b = SessionManager(db_path=db_path)
    loaded = manager_b.get_session(session.id)
    assert loaded is not None
    assert loaded.binary_path == Path("/bin/true")
    assert loaded.args == ["--test"]
    assert loaded.env == {"KEY": "value"}
    assert loaded.state == SessionState.pending_approval

    db_path.unlink()


def test_get_session():
    manager = SessionManager(db_path=Path(":memory:"))
    session = manager.create_session(
        binary_path=Path("/bin/true"),
        args=[],
        working_directory=Path("/tmp"),
    )

    retrieved = manager.get_session(session.id)
    assert retrieved is not None
    assert retrieved.id == session.id

    assert manager.get_session("nonexistent") is None


def test_get_session_status():
    manager = SessionManager(db_path=Path(":memory:"))
    session = manager.create_session(
        binary_path=Path("/bin/ls"),
        args=["-la"],
        working_directory=Path("/tmp"),
    )

    status = manager.get_session_status(session.id)
    assert status["session_id"] == session.id
    assert status["state"] == "pending_approval"
    assert status["binary_path"] == "/bin/ls"
    assert status["args"] == ["-la"]
    assert status["run_count"] == 0
    assert status["latest_run_id"] is None


def test_get_session_status_with_runs():
    manager = SessionManager(db_path=Path(":memory:"))
    session = manager.create_session(
        binary_path=Path("/bin/true"),
        args=[],
        working_directory=Path("/tmp"),
    )

    run = Run(
        id=1,
        started_at=datetime(2024, 1, 1, 12, 0, 0),
        duration_s=1.5,
        sample_count=100,
        profile_path=Path("/tmp/profile.json"),
        status=RunStatus.success,
        exit_code=0,
        stdout="",
        stderr="",
    )
    session.runs.append(run)
    manager._save_run(session.id, run)

    status = manager.get_session_status(session.id)
    assert status["run_count"] == 1
    assert status["latest_run_id"] == 1


def test_get_session_status_not_found():
    manager = SessionManager(db_path=Path(":memory:"))
    status = manager.get_session_status("nonexistent")
    assert "error" in status


def test_approve_session_correct_hash():
    manager = SessionManager(db_path=Path(":memory:"))
    session = manager.create_session(
        binary_path=Path("/bin/true"),
        args=["--help"],
        working_directory=Path("/tmp"),
    )

    assert session.state == SessionState.pending_approval

    result = manager.approve_session(session.id, session.command_hash)
    assert result is True
    assert session.state == SessionState.approved


def test_approve_session_wrong_hash():
    manager = SessionManager(db_path=Path(":memory:"))
    session = manager.create_session(
        binary_path=Path("/bin/true"),
        args=["--help"],
        working_directory=Path("/tmp"),
    )

    result = manager.approve_session(session.id, "wronghash")
    assert result is False
    assert session.state == SessionState.pending_approval


def test_approve_session_not_pending():
    manager = SessionManager(db_path=Path(":memory:"))
    session = manager.create_session(
        binary_path=Path("/bin/true"),
        args=[],
        working_directory=Path("/tmp"),
        auto_approve=True,
    )

    result = manager.approve_session(session.id, session.command_hash)
    assert result is False


def test_approve_session_nonexistent():
    manager = SessionManager(db_path=Path(":memory:"))
    result = manager.approve_session("nonexistent", "hash")
    assert result is False


def test_approve_session_persists():
    db_path = Path("/tmp/test_approve_session.db")
    if db_path.exists():
        db_path.unlink()

    manager_a = SessionManager(db_path=db_path)
    session = manager_a.create_session(
        binary_path=Path("/bin/true"),
        args=[],
        working_directory=Path("/tmp"),
    )
    manager_a.approve_session(session.id, session.command_hash)

    manager_b = SessionManager(db_path=db_path)
    loaded = manager_b.get_session(session.id)
    assert loaded is not None
    assert loaded.state == SessionState.approved

    db_path.unlink()


def test_reject_session():
    manager = SessionManager(db_path=Path(":memory:"))
    session = manager.create_session(
        binary_path=Path("/bin/true"),
        args=[],
        working_directory=Path("/tmp"),
    )

    result = manager.reject_session(session.id)
    assert result is True
    assert manager.get_session(session.id) is None


def test_reject_session_not_pending():
    manager = SessionManager(db_path=Path(":memory:"))
    session = manager.create_session(
        binary_path=Path("/bin/true"),
        args=[],
        working_directory=Path("/tmp"),
        auto_approve=True,
    )

    result = manager.reject_session(session.id)
    assert result is False
    assert manager.get_session(session.id) is not None


def test_reject_session_nonexistent():
    manager = SessionManager(db_path=Path(":memory:"))
    result = manager.reject_session("nonexistent")
    assert result is False


def test_reject_session_persists():
    db_path = Path("/tmp/test_reject_session.db")
    if db_path.exists():
        db_path.unlink()

    manager_a = SessionManager(db_path=db_path)
    session = manager_a.create_session(
        binary_path=Path("/bin/true"),
        args=[],
        working_directory=Path("/tmp"),
    )
    manager_a.reject_session(session.id)

    manager_b = SessionManager(db_path=db_path)
    assert manager_b.get_session(session.id) is None

    db_path.unlink()


def test_list_sessions():
    manager = SessionManager(db_path=Path(":memory:"))
    session1 = manager.create_session(
        binary_path=Path("/bin/true"),
        args=[],
        working_directory=Path("/tmp"),
    )
    session2 = manager.create_session(
        binary_path=Path("/bin/ls"),
        args=[],
        working_directory=Path("/tmp"),
    )
    session3 = manager.create_session(
        binary_path=Path("/bin/cat"),
        args=[],
        working_directory=Path("/tmp"),
    )

    sessions = manager.list_sessions()
    assert len(sessions) == 3
    ids = [s.id for s in sessions]
    assert session1.id in ids
    assert session2.id in ids
    assert session3.id in ids


def test_list_sessions_empty():
    manager = SessionManager(db_path=Path(":memory:"))
    sessions = manager.list_sessions()
    assert sessions == []


def test_destroy_session():
    manager = SessionManager(db_path=Path(":memory:"))
    session = manager.create_session(
        binary_path=Path("/bin/true"),
        args=[],
        working_directory=Path("/tmp"),
    )

    result = manager.destroy_session(session.id)
    assert result is True
    assert manager.get_session(session.id) is None


def test_destroy_session_nonexistent():
    manager = SessionManager(db_path=Path(":memory:"))
    result = manager.destroy_session("nonexistent")
    assert result is False


def test_destroy_session_with_profile_deletion():
    manager = SessionManager(db_path=Path(":memory:"))

    with tempfile.TemporaryDirectory() as tmpdir:
        profile_path = Path(tmpdir) / "profile.json"
        profile_path.write_text("{}")

        session = manager.create_session(
            binary_path=Path("/bin/true"),
            args=[],
            working_directory=Path("/tmp"),
        )
        run = Run(
            id=1,
            started_at=datetime(2024, 1, 1, 12, 0, 0),
            duration_s=1.0,
            sample_count=100,
            profile_path=profile_path,
            status=RunStatus.success,
            exit_code=0,
            stdout="",
            stderr="",
        )
        session.runs.append(run)

        assert profile_path.exists()
        result = manager.destroy_session(session.id, delete_profiles=True)
        assert result is True
        assert not profile_path.exists()


def test_destroy_session_persists():
    db_path = Path("/tmp/test_destroy_session.db")
    if db_path.exists():
        db_path.unlink()

    manager_a = SessionManager(db_path=db_path)
    session = manager_a.create_session(
        binary_path=Path("/bin/true"),
        args=[],
        working_directory=Path("/tmp"),
    )
    manager_a.destroy_session(session.id)

    manager_b = SessionManager(db_path=db_path)
    assert manager_b.get_session(session.id) is None

    db_path.unlink()


def test_session_id_collision_handling():
    manager = SessionManager(db_path=Path(":memory:"))

    session1 = manager.create_session(
        binary_path=Path("/bin/true"),
        args=[],
        working_directory=Path("/tmp"),
    )
    session2 = manager.create_session(
        binary_path=Path("/bin/ls"),
        args=[],
        working_directory=Path("/tmp"),
    )
    session3 = manager.create_session(
        binary_path=Path("/bin/cat"),
        args=[],
        working_directory=Path("/tmp"),
    )

    assert session1.id != session2.id
    assert session2.id != session3.id
    assert session1.id != session3.id

    assert len(set([session1.id, session2.id, session3.id])) == 3


def test_session_id_format():
    manager = SessionManager(db_path=Path(":memory:"))

    for _ in range(10):
        session = manager.create_session(
            binary_path=Path("/bin/true"),
            args=[],
            working_directory=Path("/tmp"),
        )
        assert len(session.id) == 6
        assert session.id.isalnum()
        assert (
            session.id.islower()
            or session.id.isdigit()
            or all(c.islower() or c.isdigit() for c in session.id)
        )


def test_setup_script_snapshot():
    manager = SessionManager(db_path=Path(":memory:"))

    with tempfile.TemporaryDirectory() as tmpdir:
        setup_script = Path(tmpdir) / "setup.sh"
        setup_content = "#!/bin/bash\nset -e\necho 'setup'"
        setup_script.write_text(setup_content)

        session = manager.create_session(
            binary_path=Path("/bin/true"),
            args=[],
            working_directory=Path("/tmp"),
            setup_script_path=setup_script,
        )

        assert session.setup_script_snapshot == setup_content

        setup_script.write_text("MODIFIED CONTENT")

        assert session.setup_script_snapshot == setup_content


def test_concurrent_run_requests_queue_and_execute_sequentially():
    manager = SessionManager(db_path=Path(":memory:"))
    session = manager.create_session(
        binary_path=Path("/bin/true"),
        args=[],
        working_directory=Path("/tmp"),
        auto_approve=True,
    )

    execution_order = []
    results = []

    async def simulate_run(session_id: str, delay: float, run_id: int):
        acquired = await manager.acquire_run_lock(session_id)
        results.append(("acquired", run_id, acquired))
        execution_order.append(run_id)
        assert manager.is_session_running(session_id)
        await asyncio.sleep(delay)
        await manager.release_run_lock()
        results.append(("released", run_id))

    async def run_test():
        task1 = asyncio.create_task(simulate_run(session.id, 0.05, 1))
        task2 = asyncio.create_task(simulate_run(session.id, 0.05, 2))
        task3 = asyncio.create_task(simulate_run(session.id, 0.05, 3))

        await asyncio.gather(task1, task2, task3)

    asyncio.run(run_test())

    assert execution_order == [1, 2, 3]


def test_run_ids_increment_correctly_per_session():
    manager = SessionManager(db_path=Path(":memory:"))

    session1 = manager.create_session(
        binary_path=Path("/bin/true"),
        args=[],
        working_directory=Path("/tmp"),
        auto_approve=True,
    )
    session2 = manager.create_session(
        binary_path=Path("/bin/ls"),
        args=[],
        working_directory=Path("/tmp"),
        auto_approve=True,
    )

    assert manager.get_next_run_id(session1.id) == 1
    assert manager.get_next_run_id(session2.id) == 1

    run1 = Run(
        id=1,
        started_at=datetime(2024, 1, 1, 12, 0, 0),
        duration_s=1.0,
        sample_count=100,
        profile_path=Path("/tmp/profile1.json"),
        status=RunStatus.success,
        exit_code=0,
        stdout="",
        stderr="",
    )
    session1.runs.append(run1)

    assert manager.get_next_run_id(session1.id) == 2
    assert manager.get_next_run_id(session2.id) == 1

    run2 = Run(
        id=2,
        started_at=datetime(2024, 1, 1, 12, 1, 0),
        duration_s=1.5,
        sample_count=200,
        profile_path=Path("/tmp/profile2.json"),
        status=RunStatus.success,
        exit_code=0,
        stdout="",
        stderr="",
    )
    session1.runs.append(run2)

    assert manager.get_next_run_id(session1.id) == 3
    assert manager.get_next_run_id(session2.id) == 1


def test_cannot_destroy_session_with_running_profile():
    manager = SessionManager(db_path=Path(":memory:"))
    session = manager.create_session(
        binary_path=Path("/bin/true"),
        args=[],
        working_directory=Path("/tmp"),
        auto_approve=True,
    )

    async def run_test():
        await manager.acquire_run_lock(session.id)

        assert manager.is_session_running(session.id)
        result = manager.destroy_session(session.id)
        assert result is False
        assert manager.get_session(session.id) is not None

        await manager.release_run_lock()

        assert not manager.is_session_running(session.id)
        result = manager.destroy_session(session.id)
        assert result is True
        assert manager.get_session(session.id) is None

    asyncio.run(run_test())


def test_is_session_running():
    manager = SessionManager(db_path=Path(":memory:"))
    session1 = manager.create_session(
        binary_path=Path("/bin/true"),
        args=[],
        working_directory=Path("/tmp"),
        auto_approve=True,
    )
    session2 = manager.create_session(
        binary_path=Path("/bin/ls"),
        args=[],
        working_directory=Path("/tmp"),
        auto_approve=True,
    )

    assert not manager.is_session_running(session1.id)
    assert not manager.is_session_running(session2.id)

    async def run_test():
        await manager.acquire_run_lock(session1.id)

        assert manager.is_session_running(session1.id)
        assert not manager.is_session_running(session2.id)

        await manager.release_run_lock()

        assert not manager.is_session_running(session1.id)
        assert not manager.is_session_running(session2.id)

    asyncio.run(run_test())


def test_get_profile_path():
    manager = SessionManager(db_path=Path(":memory:"))
    session = manager.create_session(
        binary_path=Path("/bin/true"),
        args=[],
        working_directory=Path("/tmp"),
    )

    path = manager.get_profile_path(session.id, 1)
    expected_base = Path.home() / ".local" / "share" / "samply-mcp" / "profiles" / session.id
    assert path.parent == expected_base
    assert path.name == "1.json"

    path2 = manager.get_profile_path(session.id, 5)
    assert path2.name == "5.json"
    assert path2.parent == expected_base


def test_profile_directory_created():
    manager = SessionManager(db_path=Path(":memory:"))
    session = manager.create_session(
        binary_path=Path("/bin/true"),
        args=[],
        working_directory=Path("/tmp"),
    )

    profile_dir = Path.home() / ".local" / "share" / "samply-mcp" / "profiles" / session.id
    if profile_dir.exists():
        for f in profile_dir.iterdir():
            f.unlink()
        profile_dir.rmdir()

    assert not profile_dir.exists()
    manager.get_profile_path(session.id, 1)
    assert profile_dir.exists()


def test_gc_sessions_destroys_old_sessions():
    manager = SessionManager(db_path=Path(":memory:"))

    old_session = manager.create_session(
        binary_path=Path("/bin/true"),
        args=["old"],
        working_directory=Path("/tmp"),
    )
    old_run = Run(
        id=1,
        started_at=datetime.now() - timedelta(days=10),
        duration_s=1.0,
        sample_count=100,
        profile_path=Path("/tmp/old.json"),
        status=RunStatus.success,
        exit_code=0,
        stdout="",
        stderr="",
    )
    manager.add_run(old_session.id, old_run)

    recent_session = manager.create_session(
        binary_path=Path("/bin/true"),
        args=["recent"],
        working_directory=Path("/tmp"),
    )
    old_run = Run(
        id=1,
        started_at=datetime.now() - timedelta(days=10),
        duration_s=1.0,
        sample_count=100,
        profile_path=Path("/tmp/old.json"),
        status=RunStatus.success,
        exit_code=0,
        stdout="",
        stderr="",
    )
    manager.add_run(old_session.id, old_run)

    recent_session = manager.create_session(
        binary_path=Path("/bin/true"),
        args=["recent"],
        working_directory=Path("/tmp"),
    )
    recent_run = Run(
        id=1,
        started_at=datetime.now() - timedelta(days=2),
        duration_s=1.0,
        sample_count=100,
        profile_path=Path("/tmp/recent.json"),
        status=RunStatus.success,
        exit_code=0,
        stdout="",
        stderr="",
    )
    manager.add_run(recent_session.id, recent_run)

    manager.create_session(
        binary_path=Path("/bin/true"),
        args=["no-runs"],
        working_directory=Path("/tmp"),
    )

    destroyed = manager.gc_sessions(max_age_days=7)

    assert old_session.id in destroyed
    assert recent_session.id not in destroyed
    assert manager.get_session(recent_session.id) is not None
    assert manager.get_session(old_session.id) is None


def test_gc_sessions_destroys_no_run_sessions_older_than_cutoff():
    manager = SessionManager(db_path=Path(":memory:"))

    old_no_runs = manager.create_session(
        binary_path=Path("/bin/true"),
        args=["old-no-runs"],
        working_directory=Path("/tmp"),
    )
    old_no_runs.created_at = datetime.now() - timedelta(days=10)
    manager._save_session(old_no_runs)

    new_no_runs = manager.create_session(
        binary_path=Path("/bin/true"),
        args=["new-no-runs"],
        working_directory=Path("/tmp"),
    )

    destroyed = manager.gc_sessions(max_age_days=7)

    assert old_no_runs.id in destroyed
    assert new_no_runs.id not in destroyed


def test_gc_sessions_custom_max_age():
    manager = SessionManager(db_path=Path(":memory:"))

    session = manager.create_session(
        binary_path=Path("/bin/true"),
        args=["test"],
        working_directory=Path("/tmp"),
    )
    run = Run(
        id=1,
        started_at=datetime.now() - timedelta(days=3),
        duration_s=1.0,
        sample_count=100,
        profile_path=Path("/tmp/test.json"),
        status=RunStatus.success,
        exit_code=0,
        stdout="",
        stderr="",
    )
    manager.add_run(session.id, run)

    destroyed = manager.gc_sessions(max_age_days=7)
    assert session.id not in destroyed

    destroyed = manager.gc_sessions(max_age_days=2)
    assert session.id in destroyed


def test_get_last_run_time():
    manager = SessionManager(db_path=Path(":memory:"))

    session = manager.create_session(
        binary_path=Path("/bin/true"),
        args=["test"],
        working_directory=Path("/tmp"),
    )

    assert manager.get_last_run_time(session.id) is None

    run1 = Run(
        id=1,
        started_at=datetime.now() - timedelta(days=2),
        duration_s=1.0,
        sample_count=100,
        profile_path=Path("/tmp/run1.json"),
        status=RunStatus.success,
        exit_code=0,
        stdout="",
        stderr="",
    )
    manager.add_run(session.id, run1)

    run2 = Run(
        id=2,
        started_at=datetime.now() - timedelta(days=1),
        duration_s=1.0,
        sample_count=100,
        profile_path=Path("/tmp/run2.json"),
        status=RunStatus.success,
        exit_code=0,
        stdout="",
        stderr="",
    )
    manager.add_run(session.id, run2)

    last_run = manager.get_last_run_time(session.id)
    assert last_run is not None
    assert last_run == run2.started_at
