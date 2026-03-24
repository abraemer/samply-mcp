"""Integration tests for end-to-end workflow."""

import os
import tempfile
from datetime import datetime
from pathlib import Path
from unittest import mock

import pytest

from samply_mcp import server
from samply_mcp.repl import REPL
from samply_mcp.session import Run, RunStatus, SessionState
from samply_mcp.session_manager import SessionManager


class TestEndToEndIntegration:
    async def test_full_workflow_create_approve_run_query(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            binary = Path(tmpdir) / "test_binary"
            binary.write_text("#!/bin/bash\necho 'hello world'\n")
            os.chmod(binary, 0o755)

            manager = SessionManager(db_path=":memory:")

            session = manager.create_session(
                binary_path=binary,
                args=["--test"],
                working_directory=Path(tmpdir),
                env={"TEST_VAR": "test_value"},
            )

            assert session.state == SessionState.pending_approval

            manager.approve_session(session.id, session.command_hash)

            updated = manager.get_session(session.id)
            assert updated is not None
            assert updated.state == SessionState.approved

            with (
                mock.patch.object(server, "_get_manager", return_value=manager),
                mock.patch.object(server, "check_perf_paranoid", return_value=(True, 1)),
            ):
                result = await server.run(session_id=session.id)

            assert "error" not in result or result.get("error") is None
            assert result["run_id"] == 1
            assert result["status"] == "success"

            run_record = updated.runs[0]
            assert run_record.id == 1
            assert run_record.status == RunStatus.success

    async def test_session_created_via_mcp_visible_in_repl(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            binary = Path(tmpdir) / "test_binary"
            binary.write_text("#!/bin/bash\necho 'test'\n")
            os.chmod(binary, 0o755)

            manager = SessionManager(db_path=":memory:")

            with (
                mock.patch.object(server, "_get_manager", return_value=manager),
                mock.patch.object(server, "_auto_approve", False, create=True),
            ):
                result = await server.create_session(
                    binary_path=str(binary),
                    args=["--arg1"],
                    working_directory=tmpdir,
                )

            assert "error" not in result
            session_id = result["session_id"]

            repl = REPL(manager)
            sessions = repl.manager.list_sessions()

            assert len(sessions) == 1
            assert sessions[0].id == session_id
            assert sessions[0].state == SessionState.pending_approval

    async def test_session_approved_via_repl_runnable_via_mcp(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            binary = Path(tmpdir) / "test_binary"
            binary.write_text("#!/bin/bash\necho 'test'\n")
            os.chmod(binary, 0o755)

            manager = SessionManager(db_path=":memory:")

            with (
                mock.patch.object(server, "_get_manager", return_value=manager),
                mock.patch.object(server, "_auto_approve", False, create=True),
            ):
                create_result = await server.create_session(
                    binary_path=str(binary),
                    args=[],
                    working_directory=tmpdir,
                )

            session_id = create_result["session_id"]

            session = manager.get_session(session_id)
            assert session is not None
            assert session.state == SessionState.pending_approval

            repl = REPL(manager)
            await repl.handle_command(f"approve {session_id} {session.command_hash[:16]}")

            updated = manager.get_session(session_id)
            assert updated is not None
            assert updated.state == SessionState.approved

            with (
                mock.patch.object(server, "_get_manager", return_value=manager),
                mock.patch.object(server, "check_perf_paranoid", return_value=(True, 1)),
            ):
                run_result = await server.run(session_id=session_id)

            assert "error" not in run_result or run_result.get("error") is None
            assert run_result["status"] == "success"

    async def test_repl_shows_sessions_created_by_mcp(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            binary = Path(tmpdir) / "test_binary"
            binary.write_text("#!/bin/bash\necho 'test'\n")
            os.chmod(binary, 0o755)

            manager = SessionManager(db_path=":memory:")

            with (
                mock.patch.object(server, "_get_manager", return_value=manager),
                mock.patch.object(server, "_auto_approve", False, create=True),
            ):
                await server.create_session(
                    binary_path=str(binary),
                    args=["--first"],
                    working_directory=tmpdir,
                )
                await server.create_session(
                    binary_path=str(binary),
                    args=["--second"],
                    working_directory=tmpdir,
                )

            sessions = manager.list_sessions()
            assert len(sessions) == 2

            for s in sessions:
                assert s.state == SessionState.pending_approval

    async def test_full_workflow_with_query_tools(self) -> None:
        ref_profile = Path(__file__).parent.parent / "reference" / "profile.json"
        if not ref_profile.exists():
            pytest.skip("reference/profile.json not found")

        with tempfile.TemporaryDirectory() as tmpdir:
            binary = Path(tmpdir) / "test_binary"
            binary.write_text("#!/bin/bash\necho 'test'\n")
            os.chmod(binary, 0o755)

            manager = SessionManager(db_path=":memory:")

            with (
                mock.patch.object(server, "_get_manager", return_value=manager),
                mock.patch.object(server, "_auto_approve", False, create=True),
            ):
                create_result = await server.create_session(
                    binary_path=str(binary),
                    args=[],
                    working_directory=tmpdir,
                )

            session_id = create_result["session_id"]
            session = manager.get_session(session_id)
            assert session is not None

            manager.approve_session(session_id, session.command_hash)

            run_record = Run(
                id=1,
                started_at=datetime.now(),
                duration_s=1.5,
                sample_count=100,
                profile_path=ref_profile,
                status=RunStatus.success,
                exit_code=0,
                stdout="test output",
                stderr="",
            )
            manager.add_run(session_id, run_record)

            with mock.patch.object(server, "_get_manager", return_value=manager):
                summary = await server.get_run_summary(session_id=session_id)
                assert "error" not in summary
                assert summary["run_id"] == 1
                assert len(summary["top_functions"]) > 0

                hot = await server.get_hot_functions(session_id=session_id, top_n=5)
                assert isinstance(hot, list)
                assert len(hot) <= 5


class TestSharedSessionManagerInstance:
    def test_repl_and_mcp_share_same_manager(self) -> None:
        manager = SessionManager(db_path=":memory:")

        repl = REPL(manager)

        assert repl.manager is manager

    async def test_state_changes_visible_across_surfaces(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            binary = Path(tmpdir) / "test_binary"
            binary.write_text("#!/bin/bash\necho 'test'\n")
            os.chmod(binary, 0o755)

            manager = SessionManager(db_path=":memory:")

            session = manager.create_session(
                binary_path=binary,
                args=[],
                working_directory=Path(tmpdir),
            )

            assert session.state == SessionState.pending_approval

            repl = REPL(manager)
            await repl.handle_command(f"approve {session.id} {session.command_hash[:16]}")

            updated = manager.get_session(session.id)
            assert updated is not None
            assert updated.state == SessionState.approved

            with (
                mock.patch.object(server, "_get_manager", return_value=manager),
                mock.patch.object(server, "check_perf_paranoid", return_value=(True, 1)),
            ):
                result = await server.run(session_id=session.id)

            assert "error" not in result or result.get("error") is None
