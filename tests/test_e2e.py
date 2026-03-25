"""End-to-end tests for session approval and setup script validation.

These tests mock out the actual samply execution to test the session
management logic in isolation without requiring samply to be installed.
"""

import json
import os
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from samply_mcp import server
from samply_mcp.runner import SamplyResult, SetupScriptResult
from samply_mcp.session import RunStatus
from samply_mcp.session_manager import SessionManager


def make_mock_profile() -> dict:
    return {
        "meta": {"interval": 1.0},
        "threads": [
            {
                "isMainThread": True,
                "samples": {"stack": [0, 0, 0], "timeDeltas": [100, 100, 100], "length": 3},
                "frameTable": {"func": [0], "inlineDepth": [0], "length": 1},
                "funcTable": {"name": [0]},
                "stackTable": {"frame": [0], "prefix": [None], "length": 1},
                "stringArray": ["main"],
            }
        ],
    }


def write_mock_profile(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(make_mock_profile()))


class TestApprovalFlow:
    @pytest.mark.asyncio
    async def test_create_session_flow_unapproved_then_approved(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            binary = Path(tmpdir) / "test_binary"
            binary.write_text("#!/bin/bash\necho hello\n")
            os.chmod(binary, 0o755)

            manager = SessionManager(db_path=":memory:")

            with (
                mock.patch.object(server, "_get_manager", return_value=manager),
                mock.patch.object(server, "_auto_approve", False, create=True),
            ):
                result = await server.create_session(
                    binary_path=str(binary),
                    args=[],
                    working_directory=tmpdir,
                )

            assert "error" not in result
            session_id = result["session_id"]
            command_hash = result["command_hash"]
            assert result["status"] == "pending_approval"

            with mock.patch.object(server, "_get_manager", return_value=manager):
                run_result = await server.run(session_id=session_id)

            assert "error" in run_result
            assert "not approved" in run_result["error"]
            assert run_result["state"] == "pending_approval"

            approved = manager.approve_session(session_id, command_hash)
            assert approved is True

            session = manager.get_session(session_id)
            assert session is not None
            assert session.state.value == "approved"

            async def mock_run_samply(*args, **kwargs):
                profile_path = kwargs.get("profile_path") or args[2]
                write_mock_profile(profile_path)
                return SamplyResult(
                    success=True,
                    profile_path=profile_path,
                    sample_count=3,
                    duration_s=0.5,
                    stdout="hello\n",
                    stderr="",
                    samply_stdout="",
                    samply_stderr="",
                    exit_code=0,
                    status=RunStatus.success,
                    log_path=None,
                )

            with (
                mock.patch.object(server, "_get_manager", return_value=manager),
                mock.patch.object(server, "check_perf_paranoid", return_value=(True, 1)),
                mock.patch.object(server, "run_samply", side_effect=mock_run_samply),
            ):
                run_result = await server.run(session_id=session_id)

            assert "error" not in run_result or run_result.get("error") is None
            assert run_result["status"] == "success"
            assert run_result["exit_code"] == 0

    @pytest.mark.asyncio
    async def test_approve_with_wrong_hash_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            binary = Path(tmpdir) / "test_binary"
            binary.write_text("#!/bin/bash\necho hello\n")
            os.chmod(binary, 0o755)

            manager = SessionManager(db_path=":memory:")

            with (
                mock.patch.object(server, "_get_manager", return_value=manager),
                mock.patch.object(server, "_auto_approve", False, create=True),
            ):
                result = await server.create_session(
                    binary_path=str(binary),
                    args=[],
                    working_directory=tmpdir,
                )

            session_id = result["session_id"]

            approved = manager.approve_session(session_id, "wrong_hash")
            assert approved is False

            session = manager.get_session(session_id)
            assert session is not None
            assert session.state.value == "pending_approval"

            with mock.patch.object(server, "_get_manager", return_value=manager):
                run_result = await server.run(session_id=session_id)

            assert "error" in run_result
            assert "not approved" in run_result["error"]

    @pytest.mark.asyncio
    async def test_auto_approve_bypasses_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            binary = Path(tmpdir) / "test_binary"
            binary.write_text("#!/bin/bash\necho hello\n")
            os.chmod(binary, 0o755)

            manager = SessionManager(db_path=":memory:")

            async def mock_run_samply(*args, **kwargs):
                profile_path = kwargs.get("profile_path") or args[2]
                write_mock_profile(profile_path)
                return SamplyResult(
                    success=True,
                    profile_path=profile_path,
                    sample_count=3,
                    duration_s=0.5,
                    stdout="hello\n",
                    stderr="",
                    samply_stdout="",
                    samply_stderr="",
                    exit_code=0,
                    status=RunStatus.success,
                    log_path=None,
                )

            with (
                mock.patch.object(server, "_get_manager", return_value=manager),
                mock.patch.object(server, "_auto_approve", True, create=True),
                mock.patch.object(server, "check_perf_paranoid", return_value=(True, 1)),
                mock.patch.object(server, "run_samply", side_effect=mock_run_samply),
            ):
                result = await server.create_session(
                    binary_path=str(binary),
                    args=[],
                    working_directory=tmpdir,
                )

            assert result["status"] == "approved"

            with (
                mock.patch.object(server, "_get_manager", return_value=manager),
                mock.patch.object(server, "check_perf_paranoid", return_value=(True, 1)),
                mock.patch.object(server, "run_samply", side_effect=mock_run_samply),
            ):
                run_result = await server.run(session_id=result["session_id"])

            assert "error" not in run_result or run_result.get("error") is None
            assert run_result["status"] == "success"


class TestSetupScriptValidation:
    @pytest.mark.asyncio
    async def test_setup_script_snapshot_used_not_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            binary = Path(tmpdir) / "test_binary"
            binary.write_text("#!/bin/bash\necho from_binary\n")
            os.chmod(binary, 0o755)

            setup_script = Path(tmpdir) / "setup.sh"
            setup_script.write_text("#!/bin/bash\necho original_setup\n")
            os.chmod(setup_script, 0o755)

            manager = SessionManager(db_path=":memory:")

            with (
                mock.patch.object(server, "_get_manager", return_value=manager),
                mock.patch.object(server, "_auto_approve", False, create=True),
            ):
                result = await server.create_session(
                    binary_path=str(binary),
                    args=[],
                    setup_script_path=str(setup_script),
                    working_directory=tmpdir,
                )

            session_id = result["session_id"]
            command_hash = result["command_hash"]
            assert result["setup_script"] == "#!/bin/bash\necho original_setup\n"

            manager.approve_session(session_id, command_hash)

            setup_scripts_executed = []

            async def mock_execute_setup(*args, **kwargs):
                setup_scripts_executed.append(args[0] if args else kwargs.get("script_content"))
                return SetupScriptResult(success=True, stdout="setup ok\n", stderr="", exit_code=0)

            async def mock_run_samply(*args, **kwargs):
                profile_path = kwargs.get("profile_path") or args[2]
                write_mock_profile(profile_path)
                return SamplyResult(
                    success=True,
                    profile_path=profile_path,
                    sample_count=3,
                    duration_s=0.5,
                    stdout="from_binary\n",
                    stderr="",
                    samply_stdout="",
                    samply_stderr="",
                    exit_code=0,
                    status=RunStatus.success,
                    log_path=None,
                )

            with (
                mock.patch.object(server, "_get_manager", return_value=manager),
                mock.patch.object(server, "check_perf_paranoid", return_value=(True, 1)),
                mock.patch.object(server, "execute_setup_script", side_effect=mock_execute_setup),
                mock.patch.object(server, "run_samply", side_effect=mock_run_samply),
            ):
                run_result = await server.run(session_id=session_id)

            assert "error" not in run_result or run_result.get("error") is None
            assert run_result["status"] == "success"
            assert setup_scripts_executed[-1] == "#!/bin/bash\necho original_setup\n"

            setup_script.write_text("#!/bin/bash\nexit 1\n")

            with (
                mock.patch.object(server, "_get_manager", return_value=manager),
                mock.patch.object(server, "check_perf_paranoid", return_value=(True, 1)),
                mock.patch.object(server, "execute_setup_script", side_effect=mock_execute_setup),
                mock.patch.object(server, "run_samply", side_effect=mock_run_samply),
            ):
                run_result2 = await server.run(session_id=session_id)

            assert "error" not in run_result2 or run_result2.get("error") is None
            assert run_result2["status"] == "success"
            assert setup_scripts_executed[-1] == "#!/bin/bash\necho original_setup\n"

    @pytest.mark.asyncio
    async def test_setup_script_failure_causes_run_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            binary = Path(tmpdir) / "test_binary"
            binary.write_text("#!/bin/bash\necho from_binary\n")
            os.chmod(binary, 0o755)

            setup_script = Path(tmpdir) / "setup.sh"
            setup_script.write_text("#!/bin/bash\necho 'setup failed' >&2\nexit 42\n")
            os.chmod(setup_script, 0o755)

            manager = SessionManager(db_path=":memory:")

            with (
                mock.patch.object(server, "_get_manager", return_value=manager),
                mock.patch.object(server, "_auto_approve", False, create=True),
            ):
                result = await server.create_session(
                    binary_path=str(binary),
                    args=[],
                    setup_script_path=str(setup_script),
                    working_directory=tmpdir,
                )

            session_id = result["session_id"]
            command_hash = result["command_hash"]

            manager.approve_session(session_id, command_hash)

            async def mock_execute_setup(*args, **kwargs):
                return SetupScriptResult(
                    success=False, stdout="", stderr="setup failed\n", exit_code=42
                )

            with (
                mock.patch.object(server, "_get_manager", return_value=manager),
                mock.patch.object(server, "check_perf_paranoid", return_value=(True, 1)),
                mock.patch.object(server, "execute_setup_script", side_effect=mock_execute_setup),
            ):
                run_result = await server.run(session_id=session_id)

            assert "error" in run_result
            assert "Setup script failed" in run_result["error"]
            assert run_result["exit_code"] == 42
            assert run_result["status"] == "failed"

    @pytest.mark.asyncio
    async def test_setup_script_missing_file_uses_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            binary = Path(tmpdir) / "test_binary"
            binary.write_text("#!/bin/bash\necho from_binary\n")
            os.chmod(binary, 0o755)

            setup_script = Path(tmpdir) / "setup.sh"
            setup_script.write_text("#!/bin/bash\necho setup_ok\n")
            os.chmod(setup_script, 0o755)

            manager = SessionManager(db_path=":memory:")

            with (
                mock.patch.object(server, "_get_manager", return_value=manager),
                mock.patch.object(server, "_auto_approve", False, create=True),
            ):
                result = await server.create_session(
                    binary_path=str(binary),
                    args=[],
                    setup_script_path=str(setup_script),
                    working_directory=tmpdir,
                )

            session_id = result["session_id"]
            command_hash = result["command_hash"]
            manager.approve_session(session_id, command_hash)

            setup_script.unlink()

            async def mock_execute_setup(*args, **kwargs):
                return SetupScriptResult(success=True, stdout="setup ok\n", stderr="", exit_code=0)

            async def mock_run_samply(*args, **kwargs):
                profile_path = kwargs.get("profile_path") or args[2]
                write_mock_profile(profile_path)
                return SamplyResult(
                    success=True,
                    profile_path=profile_path,
                    sample_count=3,
                    duration_s=0.5,
                    stdout="from_binary\n",
                    stderr="",
                    samply_stdout="",
                    samply_stderr="",
                    exit_code=0,
                    status=RunStatus.success,
                    log_path=None,
                )

            with (
                mock.patch.object(server, "_get_manager", return_value=manager),
                mock.patch.object(server, "check_perf_paranoid", return_value=(True, 1)),
                mock.patch.object(server, "execute_setup_script", side_effect=mock_execute_setup),
                mock.patch.object(server, "run_samply", side_effect=mock_run_samply),
            ):
                run_result = await server.run(session_id=session_id)

            assert "error" not in run_result or run_result.get("error") is None
            assert run_result["status"] == "success"

    @pytest.mark.asyncio
    async def test_session_without_setup_script_runs_successfully(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            binary = Path(tmpdir) / "test_binary"
            binary.write_text("#!/bin/bash\necho hello\n")
            os.chmod(binary, 0o755)

            manager = SessionManager(db_path=":memory:")

            with (
                mock.patch.object(server, "_get_manager", return_value=manager),
                mock.patch.object(server, "_auto_approve", False, create=True),
            ):
                result = await server.create_session(
                    binary_path=str(binary),
                    args=[],
                    working_directory=tmpdir,
                )

            session_id = result["session_id"]
            command_hash = result["command_hash"]
            manager.approve_session(session_id, command_hash)

            async def mock_run_samply(*args, **kwargs):
                profile_path = kwargs.get("profile_path") or args[2]
                write_mock_profile(profile_path)
                return SamplyResult(
                    success=True,
                    profile_path=profile_path,
                    sample_count=3,
                    duration_s=0.5,
                    stdout="hello\n",
                    stderr="",
                    samply_stdout="",
                    samply_stderr="",
                    exit_code=0,
                    status=RunStatus.success,
                    log_path=None,
                )

            with (
                mock.patch.object(server, "_get_manager", return_value=manager),
                mock.patch.object(server, "check_perf_paranoid", return_value=(True, 1)),
                mock.patch.object(server, "run_samply", side_effect=mock_run_samply),
            ):
                run_result = await server.run(session_id=session_id)

            assert "error" not in run_result or run_result.get("error") is None
            assert run_result["status"] == "success"

    @pytest.mark.asyncio
    async def test_run_records_profile_and_run_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            binary = Path(tmpdir) / "test_binary"
            binary.write_text("#!/bin/bash\necho hello\n")
            os.chmod(binary, 0o755)

            manager = SessionManager(db_path=":memory:")

            async def mock_run_samply(*args, **kwargs):
                profile_path = kwargs.get("profile_path") or args[2]
                write_mock_profile(profile_path)
                return SamplyResult(
                    success=True,
                    profile_path=profile_path,
                    sample_count=42,
                    duration_s=1.23,
                    stdout="hello\n",
                    stderr="",
                    samply_stdout="",
                    samply_stderr="",
                    exit_code=0,
                    status=RunStatus.success,
                    log_path=None,
                )

            with (
                mock.patch.object(server, "_get_manager", return_value=manager),
                mock.patch.object(server, "_auto_approve", True, create=True),
                mock.patch.object(server, "check_perf_paranoid", return_value=(True, 1)),
                mock.patch.object(server, "run_samply", side_effect=mock_run_samply),
            ):
                result = await server.create_session(
                    binary_path=str(binary),
                    args=["--test"],
                    working_directory=tmpdir,
                )
                session_id = result["session_id"]

                run_result = await server.run(session_id=session_id)

            assert run_result["run_id"] == 1
            assert run_result["sample_count"] == 42
            assert run_result["duration_s"] == 1.23

            session = manager.get_session(session_id)
            assert session is not None
            assert len(session.runs) == 1
            assert session.runs[0].sample_count == 42

    @pytest.mark.asyncio
    async def test_multiple_runs_increment_run_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            binary = Path(tmpdir) / "test_binary"
            binary.write_text("#!/bin/bash\necho hello\n")
            os.chmod(binary, 0o755)

            manager = SessionManager(db_path=":memory:")

            async def mock_run_samply(*args, **kwargs):
                profile_path = kwargs.get("profile_path") or args[2]
                write_mock_profile(profile_path)
                return SamplyResult(
                    success=True,
                    profile_path=profile_path,
                    sample_count=1,
                    duration_s=0.1,
                    stdout="",
                    stderr="",
                    samply_stdout="",
                    samply_stderr="",
                    exit_code=0,
                    status=RunStatus.success,
                    log_path=None,
                )

            with (
                mock.patch.object(server, "_get_manager", return_value=manager),
                mock.patch.object(server, "_auto_approve", True, create=True),
                mock.patch.object(server, "check_perf_paranoid", return_value=(True, 1)),
                mock.patch.object(server, "run_samply", side_effect=mock_run_samply),
            ):
                result = await server.create_session(
                    binary_path=str(binary),
                    args=[],
                    working_directory=tmpdir,
                )
                session_id = result["session_id"]

                run1 = await server.run(session_id=session_id)
                run2 = await server.run(session_id=session_id)
                run3 = await server.run(session_id=session_id)

            assert run1["run_id"] == 1
            assert run2["run_id"] == 2
            assert run3["run_id"] == 3

            session = manager.get_session(session_id)
            assert session is not None
            assert len(session.runs) == 3
