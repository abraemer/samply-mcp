"""Tests for the REPL."""

import asyncio
import sys
from io import StringIO
from pathlib import Path
from unittest import mock

import pytest

from samply_mcp.repl import REPL, notify_session_created
from samply_mcp.session import Session, SessionState
from samply_mcp.session_manager import SessionManager


@pytest.fixture
def manager():
    return SessionManager(db_path=":memory:")


def test_repl_can_be_created(manager):
    repl = REPL(manager)
    assert repl.manager is manager
    assert repl.running is True


def test_repl_command_parsing(manager):
    repl = REPL(manager)

    asyncio.run(repl.handle_command(""))
    assert repl.running is True

    asyncio.run(repl.handle_command("help"))
    assert repl.running is True

    asyncio.run(repl.handle_command("unknown_command"))
    assert repl.running is True


def test_repl_quit_command(manager):
    repl = REPL(manager)
    assert repl.running is True

    asyncio.run(repl.handle_command("quit"))
    assert repl.running is False


def test_repl_sessions_empty(manager):
    repl = REPL(manager)
    asyncio.run(repl.handle_command("sessions"))


def test_repl_sessions_with_session(manager):
    manager.create_session(
        binary_path="/bin/true",
        args=["--help"],
        working_directory=Path("/tmp"),
        auto_approve=True,
    )

    repl = REPL(manager)
    asyncio.run(repl.handle_command("sessions"))


def test_repl_show_command(manager):
    session = manager.create_session(
        binary_path="/bin/true",
        args=["--help"],
        working_directory=Path("/tmp"),
        auto_approve=True,
    )

    repl = REPL(manager)
    asyncio.run(repl.handle_command(f"show {session.id}"))
    asyncio.run(repl.handle_command("show nonexistent"))


def test_repl_show_missing_arg(manager):
    repl = REPL(manager)
    asyncio.run(repl.handle_command("show"))


def test_repl_approve_command(manager):
    session = manager.create_session(
        binary_path="/bin/true",
        args=[],
        working_directory=Path("/tmp"),
        auto_approve=False,
    )

    assert session.state == SessionState.pending_approval

    repl = REPL(manager)

    asyncio.run(repl.handle_command(f"approve {session.id} {session.command_hash[:16]}"))

    updated = manager.get_session(session.id)
    assert updated.state == SessionState.approved


def test_repl_approve_wrong_hash(manager):
    session = manager.create_session(
        binary_path="/bin/true",
        args=[],
        working_directory=Path("/tmp"),
        auto_approve=False,
    )

    repl = REPL(manager)
    asyncio.run(repl.handle_command(f"approve {session.id} wronghash"))

    updated = manager.get_session(session.id)
    assert updated.state == SessionState.pending_approval


def test_repl_approve_missing_args(manager):
    repl = REPL(manager)
    asyncio.run(repl.handle_command("approve"))
    asyncio.run(repl.handle_command("approve abc123"))


def test_repl_reject_command(manager):
    session = manager.create_session(
        binary_path="/bin/true",
        args=[],
        working_directory=Path("/tmp"),
        auto_approve=False,
    )

    repl = REPL(manager)
    asyncio.run(repl.handle_command(f"reject {session.id}"))

    assert manager.get_session(session.id) is None


def test_repl_reject_missing_arg(manager):
    repl = REPL(manager)
    asyncio.run(repl.handle_command("reject"))


def test_repl_runs_command(manager):
    session = manager.create_session(
        binary_path="/bin/true",
        args=[],
        working_directory=Path("/tmp"),
        auto_approve=True,
    )

    repl = REPL(manager)
    asyncio.run(repl.handle_command(f"runs {session.id}"))
    asyncio.run(repl.handle_command("runs nonexistent"))


def test_repl_runs_missing_arg(manager):
    repl = REPL(manager)
    asyncio.run(repl.handle_command("runs"))


def test_repl_destroy_command(manager):
    session = manager.create_session(
        binary_path="/bin/true",
        args=[],
        working_directory=Path("/tmp"),
        auto_approve=True,
    )

    repl = REPL(manager)
    asyncio.run(repl.handle_command(f"destroy {session.id}"))

    assert manager.get_session(session.id) is None


def test_repl_destroy_missing_arg(manager):
    repl = REPL(manager)
    asyncio.run(repl.handle_command("destroy"))


def test_repl_gc_command(manager):
    repl = REPL(manager)
    asyncio.run(repl.handle_command("gc"))
    asyncio.run(repl.handle_command("gc 30"))


def test_repl_gc_invalid_arg(manager):
    repl = REPL(manager)
    asyncio.run(repl.handle_command("gc invalid"))


class TestNotifySessionCreated:
    def test_notify_session_created_basic(self, manager):
        session = manager.create_session(
            binary_path=Path("/bin/true"),
            args=["--help"],
            working_directory=Path("/tmp"),
            auto_approve=False,
        )

        captured_output = StringIO()
        with mock.patch.object(sys, "stderr", captured_output):
            notify_session_created(session)

        output = captured_output.getvalue()
        assert "[samply-mcp] New session pending approval:" in output
        assert session.id in output
        assert "/bin/true" in output
        assert "--help" in output
        assert "To approve:" in output
        assert "To reject:" in output

    def test_notify_session_created_with_setup_script(self, manager):
        session = Session(
            id="abc123",
            binary_path=Path("/bin/true"),
            args=["--input", "data.bin"],
            setup_script_path=Path("./scripts/setup.sh"),
            setup_script_snapshot="#!/bin/bash\necho 'setup'\n",
            env={"RUST_LOG": "warn"},
            working_directory=Path("/home/user/project"),
            state=SessionState.pending_approval,
        )

        captured_output = StringIO()
        with mock.patch.object(sys, "stderr", captured_output):
            notify_session_created(session)

        output = captured_output.getvalue()
        assert "Setup:   scripts/setup.sh" in output
        assert "Env:     RUST_LOG=warn" in output
        assert "Setup script contents:" in output
        assert "#!/bin/bash" in output
        assert "\u2500" * 21 in output

    def test_notify_session_created_no_args(self, manager):
        session = manager.create_session(
            binary_path=Path("/bin/true"),
            args=[],
            working_directory=Path("/tmp"),
            auto_approve=False,
        )

        captured_output = StringIO()
        with mock.patch.object(sys, "stderr", captured_output):
            notify_session_created(session)

        output = captured_output.getvalue()
        assert "Args:    (none)" in output

    def test_notify_session_created_no_setup_script(self, manager):
        session = manager.create_session(
            binary_path=Path("/bin/true"),
            args=[],
            working_directory=Path("/tmp"),
            auto_approve=False,
        )

        captured_output = StringIO()
        with mock.patch.object(sys, "stderr", captured_output):
            notify_session_created(session)

        output = captured_output.getvalue()
        assert "Setup script contents:" not in output
        assert "Setup:" not in output

    def test_notify_session_created_hash_truncated(self, manager):
        session = manager.create_session(
            binary_path=Path("/bin/true"),
            args=[],
            working_directory=Path("/tmp"),
            auto_approve=False,
        )

        captured_output = StringIO()
        with mock.patch.object(sys, "stderr", captured_output):
            notify_session_created(session)

        output = captured_output.getvalue()
        assert f"approve {session.id} {session.command_hash[:8]}" in output
        assert f"Command hash: {session.command_hash[:8]}..." in output
