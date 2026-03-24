"""Tests for the FastMCP server."""

import json
import os
import platform
import tempfile
from datetime import datetime
from pathlib import Path
from unittest import mock

import pytest

from samply_mcp import server
from samply_mcp.gecko.parser import parse_gecko_profile
from samply_mcp.session import Run, RunStatus
from samply_mcp.session_manager import SessionManager


class TestPlatformCheck:
    def test_check_platform_returns_true_on_linux(self) -> None:
        with mock.patch.object(platform, "system", return_value="Linux"):
            assert server.check_platform() is True

    def test_check_platform_returns_false_on_macos(self) -> None:
        with mock.patch.object(platform, "system", return_value="Darwin"):
            assert server.check_platform() is False

    def test_check_platform_returns_false_on_windows(self) -> None:
        with mock.patch.object(platform, "system", return_value="Windows"):
            assert server.check_platform() is False


class TestSamplyCheck:
    def test_check_samply_installed_returns_true_when_found(self) -> None:
        with mock.patch("shutil.which", return_value="/usr/bin/samply"):
            assert server.check_samply_installed() is True

    def test_check_samply_installed_returns_false_when_not_found(self) -> None:
        with mock.patch("shutil.which", return_value=None):
            assert server.check_samply_installed() is False


class TestAddr2lineCheck:
    def test_check_addr2line_installed_returns_true_when_found(self) -> None:
        with mock.patch("shutil.which", return_value="/usr/bin/addr2line"):
            assert server.check_addr2line_installed() is True

    def test_check_addr2line_installed_returns_false_when_not_found(self) -> None:
        with mock.patch("shutil.which", return_value=None):
            assert server.check_addr2line_installed() is False


class TestPerfParanoidCheck:
    def test_check_perf_paranoid_ok_when_level_1(self) -> None:
        with mock.patch("samply_mcp.server.Path") as mock_path:
            mock_instance = mock.Mock()
            mock_instance.read_text.return_value = "1\n"
            mock_path.return_value = mock_instance
            is_ok, level = server.check_perf_paranoid()
            assert is_ok is True
            assert level == 1

    def test_check_perf_paranoid_not_ok_when_level_2(self) -> None:
        with mock.patch("samply_mcp.server.Path") as mock_path:
            mock_instance = mock.Mock()
            mock_instance.read_text.return_value = "2\n"
            mock_path.return_value = mock_instance
            is_ok, level = server.check_perf_paranoid()
            assert is_ok is False
            assert level == 2

    def test_check_perf_paranoid_handles_missing_file(self) -> None:
        with mock.patch("samply_mcp.server.Path") as mock_path:
            mock_instance = mock.Mock()
            mock_instance.read_text.side_effect = OSError("File not found")
            mock_path.return_value = mock_instance
            is_ok, level = server.check_perf_paranoid()
            assert is_ok is False
            assert level == -1

    def test_check_perf_paranoid_handles_invalid_content(self) -> None:
        with mock.patch("samply_mcp.server.Path") as mock_path:
            mock_instance = mock.Mock()
            mock_instance.read_text.return_value = "invalid\n"
            mock_path.return_value = mock_instance
            is_ok, level = server.check_perf_paranoid()
            assert is_ok is False
            assert level == -1


class TestStartupValidation:
    def test_validate_startup_exits_on_non_linux(self) -> None:
        with (
            mock.patch.object(platform, "system", return_value="Darwin"),
            mock.patch.object(server, "check_samply_installed", return_value=True),
            mock.patch.object(server, "check_addr2line_installed", return_value=True),
            mock.patch.object(server, "check_perf_paranoid", return_value=(True, 1)),
            pytest.raises(SystemExit) as exc_info,
        ):
            server.validate_startup()
        assert exc_info.value.code == 1

    def test_validate_startup_exits_when_samply_missing(self) -> None:
        with (
            mock.patch.object(platform, "system", return_value="Linux"),
            mock.patch.object(server, "check_samply_installed", return_value=False),
            mock.patch.object(server, "check_addr2line_installed", return_value=True),
            mock.patch.object(server, "check_perf_paranoid", return_value=(True, 1)),
            pytest.raises(SystemExit) as exc_info,
        ):
            server.validate_startup()
        assert exc_info.value.code == 1

    def test_validate_startup_exits_when_addr2line_missing(self) -> None:
        with (
            mock.patch.object(platform, "system", return_value="Linux"),
            mock.patch.object(server, "check_samply_installed", return_value=True),
            mock.patch.object(server, "check_addr2line_installed", return_value=False),
            mock.patch.object(server, "check_perf_paranoid", return_value=(True, 1)),
            pytest.raises(SystemExit) as exc_info,
        ):
            server.validate_startup()
        assert exc_info.value.code == 1

    def test_validate_startup_exits_when_perf_paranoid_too_high(self) -> None:
        with (
            mock.patch.object(platform, "system", return_value="Linux"),
            mock.patch.object(server, "check_samply_installed", return_value=True),
            mock.patch.object(server, "check_addr2line_installed", return_value=True),
            mock.patch.object(server, "check_perf_paranoid", return_value=(False, 2)),
            pytest.raises(SystemExit) as exc_info,
        ):
            server.validate_startup()
        assert exc_info.value.code == 1

    def test_validate_startup_passes_all_checks(self) -> None:
        with (
            mock.patch.object(platform, "system", return_value="Linux"),
            mock.patch.object(server, "check_samply_installed", return_value=True),
            mock.patch.object(server, "check_addr2line_installed", return_value=True),
            mock.patch.object(server, "check_perf_paranoid", return_value=(True, 1)),
        ):
            server.validate_startup()


class TestServerImport:
    def test_server_module_can_be_imported(self) -> None:
        from samply_mcp import server as server_module

        assert hasattr(server_module, "main")
        assert hasattr(server_module, "validate_startup")
        assert hasattr(server_module, "check_platform")
        assert hasattr(server_module, "check_samply_installed")
        assert hasattr(server_module, "check_perf_paranoid")


class TestIsExecutable:
    def test_is_executable_returns_true_for_executable_file(self) -> None:
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"#!/bin/bash\n")
            temp_path = Path(f.name)
        try:
            os.chmod(temp_path, 0o755)
            assert server._is_executable(temp_path) is True
        finally:
            temp_path.unlink()

    def test_is_executable_returns_false_for_non_executable_file(self) -> None:
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"content\n")
            temp_path = Path(f.name)
        try:
            os.chmod(temp_path, 0o644)
            assert server._is_executable(temp_path) is False
        finally:
            temp_path.unlink()

    def test_is_executable_returns_false_for_nonexistent_file(self) -> None:
        assert server._is_executable(Path("/nonexistent/file")) is False

    def test_is_executable_returns_false_for_directory(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            assert server._is_executable(Path(d)) is False


class TestCreateSessionTool:
    @pytest.mark.asyncio
    async def test_create_session_success(self) -> None:
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
                    args=["--arg1", "value"],
                    env={"FOO": "bar"},
                    working_directory=tmpdir,
                )

            assert "error" not in result
            assert "session_id" in result
            assert len(result["session_id"]) == 6
            assert "command_hash" in result
            assert result["display_command"] == f"{binary} --arg1 value"
            assert result["status"] == "pending_approval"
            assert "next_steps" in result

    @pytest.mark.asyncio
    async def test_create_session_auto_approve(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            binary = Path(tmpdir) / "test_binary"
            binary.write_text("#!/bin/bash\necho hello\n")
            os.chmod(binary, 0o755)

            manager = SessionManager(db_path=":memory:")
            with (
                mock.patch.object(server, "_get_manager", return_value=manager),
                mock.patch.object(server, "_auto_approve", True, create=True),
            ):
                result = await server.create_session(
                    binary_path=str(binary),
                    args=[],
                    working_directory=tmpdir,
                )

            assert "error" not in result
            assert result["status"] == "approved"
            assert "next_steps" in result

    @pytest.mark.asyncio
    async def test_create_session_nonexistent_binary(self) -> None:
        result = await server.create_session(
            binary_path="/nonexistent/binary",
            args=[],
            working_directory="/tmp",
        )
        assert "error" in result
        assert "does not exist" in result["error"]

    @pytest.mark.asyncio
    async def test_create_session_non_executable_binary(self) -> None:
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"content\n")
            temp_path = f.name
        try:
            os.chmod(Path(temp_path), 0o644)
            result = await server.create_session(
                binary_path=temp_path,
                args=[],
                working_directory="/tmp",
            )
            assert "error" in result
            assert "not executable" in result["error"]
        finally:
            Path(temp_path).unlink()

    @pytest.mark.asyncio
    async def test_create_session_nonexistent_working_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            binary = Path(tmpdir) / "test_binary"
            binary.write_text("#!/bin/bash\necho hello\n")
            os.chmod(binary, 0o755)

            result = await server.create_session(
                binary_path=str(binary),
                args=[],
                working_directory="/nonexistent/dir",
            )
            assert "error" in result
            assert "Working directory does not exist" in result["error"]

    @pytest.mark.asyncio
    async def test_create_session_with_setup_script(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            binary = Path(tmpdir) / "test_binary"
            binary.write_text("#!/bin/bash\necho hello\n")
            os.chmod(binary, 0o755)

            setup_script = Path(tmpdir) / "setup.sh"
            setup_script.write_text("#!/bin/bash\necho setup\n")
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

            assert "error" not in result
            assert result["setup_script"] == "#!/bin/bash\necho setup\n"

    @pytest.mark.asyncio
    async def test_create_session_nonexistent_setup_script(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            binary = Path(tmpdir) / "test_binary"
            binary.write_text("#!/bin/bash\necho hello\n")
            os.chmod(binary, 0o755)

            result = await server.create_session(
                binary_path=str(binary),
                args=[],
                setup_script_path="/nonexistent/setup.sh",
                working_directory=tmpdir,
            )
            assert "error" in result
            assert "Setup script path does not exist" in result["error"]

    @pytest.mark.asyncio
    async def test_create_session_non_executable_setup_script(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            binary = Path(tmpdir) / "test_binary"
            binary.write_text("#!/bin/bash\necho hello\n")
            os.chmod(binary, 0o755)

            setup_script = Path(tmpdir) / "setup.sh"
            setup_script.write_text("#!/bin/bash\necho setup\n")
            os.chmod(setup_script, 0o644)

            result = await server.create_session(
                binary_path=str(binary),
                args=[],
                setup_script_path=str(setup_script),
                working_directory=tmpdir,
            )
            assert "error" in result
            assert "Setup script path is not executable" in result["error"]


class TestGetSessionStatusTool:
    @pytest.mark.asyncio
    async def test_get_session_status_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            binary = Path(tmpdir) / "test_binary"
            binary.write_text("#!/bin/bash\necho hello\n")
            os.chmod(binary, 0o755)

            manager = SessionManager(db_path=":memory:")
            session = manager.create_session(
                binary_path=binary,
                args=["--test"],
                working_directory=Path(tmpdir),
            )

            with mock.patch.object(server, "_get_manager", return_value=manager):
                result = await server.get_session_status(session.id)

            assert "error" not in result
            assert result["session_id"] == session.id
            assert result["state"] == "pending_approval"
            assert result["binary_path"] == str(binary)
            assert result["args"] == ["--test"]
            assert result["run_count"] == 0
            assert result["latest_run_id"] is None

    @pytest.mark.asyncio
    async def test_get_session_status_not_found(self) -> None:
        manager = SessionManager(db_path=":memory:")
        with mock.patch.object(server, "_get_manager", return_value=manager):
            result = await server.get_session_status("nonexistent")

        assert "error" in result
        assert "not found" in result["error"]


class TestDestroySessionTool:
    @pytest.mark.asyncio
    async def test_destroy_session_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            binary = Path(tmpdir) / "test_binary"
            binary.write_text("#!/bin/bash\necho hello\n")
            os.chmod(binary, 0o755)

            manager = SessionManager(db_path=":memory:")
            session = manager.create_session(
                binary_path=binary,
                args=[],
                working_directory=Path(tmpdir),
            )

            with mock.patch.object(server, "_get_manager", return_value=manager):
                result = await server.destroy_session(session.id)

            assert "error" not in result
            assert result["session_id"] == session.id
            assert result["destroyed"] is True

    @pytest.mark.asyncio
    async def test_destroy_session_not_found(self) -> None:
        manager = SessionManager(db_path=":memory:")
        with mock.patch.object(server, "_get_manager", return_value=manager):
            result = await server.destroy_session("nonexistent")

        assert "error" in result
        assert "not found" in result["error"]

    @pytest.mark.asyncio
    async def test_destroy_session_while_running(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            binary = Path(tmpdir) / "test_binary"
            binary.write_text("#!/bin/bash\necho hello\n")
            os.chmod(binary, 0o755)

            manager = SessionManager(db_path=":memory:")
            session = manager.create_session(
                binary_path=binary,
                args=[],
                working_directory=Path(tmpdir),
            )
            manager._running_session_id = session.id

            with mock.patch.object(server, "_get_manager", return_value=manager):
                result = await server.destroy_session(session.id)

            assert "error" in result
            assert "run is currently in progress" in result["error"]


class TestListSessionsTool:
    @pytest.mark.asyncio
    async def test_list_sessions_empty(self) -> None:
        manager = SessionManager(db_path=":memory:")
        with mock.patch.object(server, "_get_manager", return_value=manager):
            result = await server.list_sessions()

        assert result == []

    @pytest.mark.asyncio
    async def test_list_sessions_with_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            binary = Path(tmpdir) / "test_binary"
            binary.write_text("#!/bin/bash\necho hello\n")
            os.chmod(binary, 0o755)

            manager = SessionManager(db_path=":memory:")
            session1 = manager.create_session(
                binary_path=binary,
                args=["--arg1"],
                working_directory=Path(tmpdir),
            )
            session2 = manager.create_session(
                binary_path=binary,
                args=["--arg2"],
                working_directory=Path(tmpdir),
            )

            with mock.patch.object(server, "_get_manager", return_value=manager):
                result = await server.list_sessions()

            assert len(result) == 2
            session_ids = [s["session_id"] for s in result]
            assert session1.id in session_ids
            assert session2.id in session_ids

            for s in result:
                assert "session_id" in s
                assert "state" in s
                assert "binary_path" in s
                assert "run_count" in s
                assert "created_at" in s


class TestRunTool:
    @pytest.mark.asyncio
    async def test_run_executes_successfully_with_approved_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            binary = Path(tmpdir) / "test_binary"
            binary.write_text("#!/bin/bash\necho hello\n")
            os.chmod(binary, 0o755)

            manager = SessionManager(db_path=":memory:")
            session = manager.create_session(
                binary_path=binary,
                args=[],
                working_directory=Path(tmpdir),
                auto_approve=True,
            )

            with (
                mock.patch.object(server, "_get_manager", return_value=manager),
                mock.patch.object(server, "check_perf_paranoid", return_value=(True, 1)),
            ):
                result = await server.run(session_id=session.id)

            assert "error" not in result or result.get("error") is None
            assert result["run_id"] == 1
            assert result["status"] == "success"
            assert result["exit_code"] == 0

    @pytest.mark.asyncio
    async def test_run_fails_for_unapproved_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            binary = Path(tmpdir) / "test_binary"
            binary.write_text("#!/bin/bash\necho hello\n")
            os.chmod(binary, 0o755)

            manager = SessionManager(db_path=":memory:")
            session = manager.create_session(
                binary_path=binary,
                args=[],
                working_directory=Path(tmpdir),
                auto_approve=False,
            )

            with mock.patch.object(server, "_get_manager", return_value=manager):
                result = await server.run(session_id=session.id)

            assert "error" in result
            assert "not approved" in result["error"]
            assert result["state"] == "pending_approval"

    @pytest.mark.asyncio
    async def test_run_timeout_none_works(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            binary = Path(tmpdir) / "test_binary"
            binary.write_text("#!/bin/bash\necho hello\n")
            os.chmod(binary, 0o755)

            manager = SessionManager(db_path=":memory:")
            session = manager.create_session(
                binary_path=binary,
                args=[],
                working_directory=Path(tmpdir),
                auto_approve=True,
            )

            with (
                mock.patch.object(server, "_get_manager", return_value=manager),
                mock.patch.object(server, "check_perf_paranoid", return_value=(True, 1)),
            ):
                result = await server.run(session_id=session.id, timeout_s=None)

            assert "error" not in result or result.get("error") is None
            assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_run_output_mode_stdout(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            binary = Path(tmpdir) / "test_binary"
            binary.write_text("#!/bin/bash\necho 'test output'\n")
            os.chmod(binary, 0o755)

            manager = SessionManager(db_path=":memory:")
            session = manager.create_session(
                binary_path=binary,
                args=[],
                working_directory=Path(tmpdir),
                auto_approve=True,
            )

            with (
                mock.patch.object(server, "_get_manager", return_value=manager),
                mock.patch.object(server, "check_perf_paranoid", return_value=(True, 1)),
            ):
                result = await server.run(
                    session_id=session.id,
                    output_mode="STDOUT",
                )

            assert "error" not in result or result.get("error") is None
            assert result["stdout"] is not None
            assert "test output" in result["stdout"]

    @pytest.mark.asyncio
    async def test_run_output_mode_devnull(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            binary = Path(tmpdir) / "test_binary"
            binary.write_text("#!/bin/bash\necho 'test output'\n")
            os.chmod(binary, 0o755)

            manager = SessionManager(db_path=":memory:")
            session = manager.create_session(
                binary_path=binary,
                args=[],
                working_directory=Path(tmpdir),
                auto_approve=True,
            )

            with (
                mock.patch.object(server, "_get_manager", return_value=manager),
                mock.patch.object(server, "check_perf_paranoid", return_value=(True, 1)),
            ):
                result = await server.run(
                    session_id=session.id,
                    output_mode="DEVNULL",
                )

            assert "error" not in result or result.get("error") is None
            assert result["stdout"] is None

    @pytest.mark.asyncio
    async def test_run_output_mode_file_returns_log_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            binary = Path(tmpdir) / "test_binary"
            binary.write_text("#!/bin/bash\necho 'test output'\n")
            os.chmod(binary, 0o755)

            manager = SessionManager(db_path=":memory:")
            session = manager.create_session(
                binary_path=binary,
                args=[],
                working_directory=Path(tmpdir),
                auto_approve=True,
            )

            with (
                mock.patch.object(server, "_get_manager", return_value=manager),
                mock.patch.object(server, "check_perf_paranoid", return_value=(True, 1)),
            ):
                result = await server.run(
                    session_id=session.id,
                    output_mode="FILE",
                )

            assert "error" not in result or result.get("error") is None
            assert result["log_path"] is not None
            assert ".profiling-logs" in result["log_path"]
            log_path = Path(result["log_path"])
            assert log_path.exists()
            assert "test output" in log_path.read_text()

    @pytest.mark.asyncio
    async def test_run_perf_paranoid_too_high(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            binary = Path(tmpdir) / "test_binary"
            binary.write_text("#!/bin/bash\necho hello\n")
            os.chmod(binary, 0o755)

            manager = SessionManager(db_path=":memory:")
            session = manager.create_session(
                binary_path=binary,
                args=[],
                working_directory=Path(tmpdir),
                auto_approve=True,
            )

            with (
                mock.patch.object(server, "_get_manager", return_value=manager),
                mock.patch.object(server, "check_perf_paranoid", return_value=(False, 2)),
            ):
                result = await server.run(session_id=session.id)

            assert "error" in result
            assert "perf_event_paranoid" in result["error"]

    @pytest.mark.asyncio
    async def test_run_session_not_found(self) -> None:
        manager = SessionManager(db_path=":memory:")
        with mock.patch.object(server, "_get_manager", return_value=manager):
            result = await server.run(session_id="nonexistent")

        assert "error" in result
        assert "not found" in result["error"]

    @pytest.mark.asyncio
    async def test_run_invalid_output_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            binary = Path(tmpdir) / "test_binary"
            binary.write_text("#!/bin/bash\necho hello\n")
            os.chmod(binary, 0o755)

            manager = SessionManager(db_path=":memory:")
            session = manager.create_session(
                binary_path=binary,
                args=[],
                working_directory=Path(tmpdir),
                auto_approve=True,
            )

            with mock.patch.object(server, "_get_manager", return_value=manager):
                result = await server.run(
                    session_id=session.id,
                    output_mode="INVALID",
                )

            assert "error" in result
            assert "Invalid output_mode" in result["error"]


class TestGetRunSummaryTool:
    @pytest.mark.asyncio
    async def test_get_run_summary_success(self) -> None:
        ref_profile = Path(__file__).parent.parent / "reference" / "profile.json"
        if not ref_profile.exists():
            pytest.skip("reference/profile.json not found")

        manager = SessionManager(db_path=":memory:")
        session = manager.create_session(
            binary_path=Path("/bin/true"),
            args=[],
            working_directory=Path.cwd(),
            auto_approve=True,
        )
        run = Run(
            id=1,
            started_at=datetime.now(),
            duration_s=1.5,
            sample_count=100,
            profile_path=ref_profile,
            status=RunStatus.success,
            exit_code=0,
            stdout="",
            stderr="",
        )
        manager.add_run(session.id, run)

        with mock.patch.object(server, "_get_manager", return_value=manager):
            result = await server.get_run_summary(session_id=session.id)

        assert "error" not in result
        assert result["run_id"] == 1
        assert result["duration_s"] == 1.5
        assert result["sample_count"] == 100
        assert "top_functions" in result
        assert len(result["top_functions"]) <= 5

    @pytest.mark.asyncio
    async def test_get_run_summary_default_run_id(self) -> None:
        ref_profile = Path(__file__).parent.parent / "reference" / "profile.json"
        if not ref_profile.exists():
            pytest.skip("reference/profile.json not found")

        manager = SessionManager(db_path=":memory:")
        session = manager.create_session(
            binary_path=Path("/bin/true"),
            args=[],
            working_directory=Path.cwd(),
            auto_approve=True,
        )
        run1 = Run(
            id=1,
            started_at=datetime.now(),
            duration_s=1.0,
            sample_count=50,
            profile_path=ref_profile,
            status=RunStatus.success,
            exit_code=0,
            stdout="",
            stderr="",
        )
        run2 = Run(
            id=2,
            started_at=datetime.now(),
            duration_s=2.0,
            sample_count=100,
            profile_path=ref_profile,
            status=RunStatus.success,
            exit_code=0,
            stdout="",
            stderr="",
        )
        manager.add_run(session.id, run1)
        manager.add_run(session.id, run2)

        with mock.patch.object(server, "_get_manager", return_value=manager):
            result = await server.get_run_summary(session_id=session.id)

        assert "error" not in result
        assert result["run_id"] == 2

    @pytest.mark.asyncio
    async def test_get_run_summary_session_not_found(self) -> None:
        manager = SessionManager(db_path=":memory:")
        with mock.patch.object(server, "_get_manager", return_value=manager):
            result = await server.get_run_summary(session_id="nonexistent")

        assert "error" in result
        assert "not found" in result["error"]

    @pytest.mark.asyncio
    async def test_get_run_summary_no_runs(self) -> None:
        manager = SessionManager(db_path=":memory:")
        session = manager.create_session(
            binary_path=Path("/bin/true"),
            args=[],
            working_directory=Path.cwd(),
        )

        with mock.patch.object(server, "_get_manager", return_value=manager):
            result = await server.get_run_summary(session_id=session.id)

        assert "error" in result
        assert "No runs found" in result["error"]


class TestGetHotFunctionsTool:
    @pytest.mark.asyncio
    async def test_get_hot_functions_success(self) -> None:
        ref_profile = Path(__file__).parent.parent / "reference" / "profile.json"
        if not ref_profile.exists():
            pytest.skip("reference/profile.json not found")

        manager = SessionManager(db_path=":memory:")
        session = manager.create_session(
            binary_path=Path("/bin/true"),
            args=[],
            working_directory=Path.cwd(),
            auto_approve=True,
        )
        run = Run(
            id=1,
            started_at=datetime.now(),
            duration_s=1.0,
            sample_count=100,
            profile_path=ref_profile,
            status=RunStatus.success,
            exit_code=0,
            stdout="",
            stderr="",
        )
        manager.add_run(session.id, run)

        with mock.patch.object(server, "_get_manager", return_value=manager):
            result = await server.get_hot_functions(session_id=session.id, top_n=10)

        assert "error" not in result
        assert isinstance(result, list)
        assert len(result) <= 10
        if result:
            assert "rank" in result[0]
            assert "name" in result[0]
            assert "self_pct" in result[0]
            assert "total_pct" in result[0]
            assert "is_inlined" in result[0]

    @pytest.mark.asyncio
    async def test_get_hot_functions_default_top_n(self) -> None:
        ref_profile = Path(__file__).parent.parent / "reference" / "profile.json"
        if not ref_profile.exists():
            pytest.skip("reference/profile.json not found")

        manager = SessionManager(db_path=":memory:")
        session = manager.create_session(
            binary_path=Path("/bin/true"),
            args=[],
            working_directory=Path.cwd(),
            auto_approve=True,
        )
        run = Run(
            id=1,
            started_at=datetime.now(),
            duration_s=1.0,
            sample_count=100,
            profile_path=ref_profile,
            status=RunStatus.success,
            exit_code=0,
            stdout="",
            stderr="",
        )
        manager.add_run(session.id, run)

        with mock.patch.object(server, "_get_manager", return_value=manager):
            result = await server.get_hot_functions(session_id=session.id)

        assert "error" not in result
        assert isinstance(result, list)
        assert len(result) <= 20


class TestGetCallersTool:
    @pytest.mark.asyncio
    async def test_get_callers_success(self) -> None:
        ref_profile = Path(__file__).parent.parent / "reference" / "profile.json"
        if not ref_profile.exists():
            pytest.skip("reference/profile.json not found")

        profile = parse_gecko_profile(ref_profile)
        hot_funcs = profile.hot_functions(top_n=5)
        if not hot_funcs:
            pytest.skip("No hot functions in reference profile")

        func_name = hot_funcs[0].name

        manager = SessionManager(db_path=":memory:")
        session = manager.create_session(
            binary_path=Path("/bin/true"),
            args=[],
            working_directory=Path.cwd(),
            auto_approve=True,
        )
        run = Run(
            id=1,
            started_at=datetime.now(),
            duration_s=1.0,
            sample_count=100,
            profile_path=ref_profile,
            status=RunStatus.success,
            exit_code=0,
            stdout="",
            stderr="",
        )
        manager.add_run(session.id, run)

        with mock.patch.object(server, "_get_manager", return_value=manager):
            result = await server.get_callers(
                session_id=session.id,
                function_name=func_name,
            )

        assert "error" not in result
        assert "matched_function" in result
        assert "name" in result["matched_function"]
        assert "self_pct" in result["matched_function"]
        assert "total_pct" in result["matched_function"]
        assert "callers" in result

    @pytest.mark.asyncio
    async def test_get_callers_case_insensitive(self) -> None:
        ref_profile = Path(__file__).parent.parent / "reference" / "profile.json"
        if not ref_profile.exists():
            pytest.skip("reference/profile.json not found")

        profile = parse_gecko_profile(ref_profile)
        hot_funcs = profile.hot_functions(top_n=5)
        if not hot_funcs:
            pytest.skip("No hot functions in reference profile")

        func_name = hot_funcs[0].name.lower()

        manager = SessionManager(db_path=":memory:")
        session = manager.create_session(
            binary_path=Path("/bin/true"),
            args=[],
            working_directory=Path.cwd(),
            auto_approve=True,
        )
        run = Run(
            id=1,
            started_at=datetime.now(),
            duration_s=1.0,
            sample_count=100,
            profile_path=ref_profile,
            status=RunStatus.success,
            exit_code=0,
            stdout="",
            stderr="",
        )
        manager.add_run(session.id, run)

        with mock.patch.object(server, "_get_manager", return_value=manager):
            result = await server.get_callers(
                session_id=session.id,
                function_name=func_name,
            )

        assert "error" not in result

    @pytest.mark.asyncio
    async def test_get_callers_not_found(self) -> None:
        ref_profile = Path(__file__).parent.parent / "reference" / "profile.json"
        if not ref_profile.exists():
            pytest.skip("reference/profile.json not found")

        manager = SessionManager(db_path=":memory:")
        session = manager.create_session(
            binary_path=Path("/bin/true"),
            args=[],
            working_directory=Path.cwd(),
            auto_approve=True,
        )
        run = Run(
            id=1,
            started_at=datetime.now(),
            duration_s=1.0,
            sample_count=100,
            profile_path=ref_profile,
            status=RunStatus.success,
            exit_code=0,
            stdout="",
            stderr="",
        )
        manager.add_run(session.id, run)

        with mock.patch.object(server, "_get_manager", return_value=manager):
            result = await server.get_callers(
                session_id=session.id,
                function_name="nonexistent_function_xyz",
            )

        assert "error" in result
        assert "No function matching" in result["error"]


class TestGetCalleesTool:
    @pytest.mark.asyncio
    async def test_get_callees_success(self) -> None:
        ref_profile = Path(__file__).parent.parent / "reference" / "profile.json"
        if not ref_profile.exists():
            pytest.skip("reference/profile.json not found")

        profile = parse_gecko_profile(ref_profile)
        hot_funcs = profile.hot_functions(top_n=5)
        if not hot_funcs:
            pytest.skip("No hot functions in reference profile")

        func_name = hot_funcs[0].name

        manager = SessionManager(db_path=":memory:")
        session = manager.create_session(
            binary_path=Path("/bin/true"),
            args=[],
            working_directory=Path.cwd(),
            auto_approve=True,
        )
        run = Run(
            id=1,
            started_at=datetime.now(),
            duration_s=1.0,
            sample_count=100,
            profile_path=ref_profile,
            status=RunStatus.success,
            exit_code=0,
            stdout="",
            stderr="",
        )
        manager.add_run(session.id, run)

        with mock.patch.object(server, "_get_manager", return_value=manager):
            result = await server.get_callees(
                session_id=session.id,
                function_name=func_name,
            )

        assert "error" not in result
        assert "matched_function" in result
        assert "name" in result["matched_function"]
        assert "self_pct" in result["matched_function"]
        assert "total_pct" in result["matched_function"]
        assert "callees" in result

    @pytest.mark.asyncio
    async def test_get_callees_not_found(self) -> None:
        ref_profile = Path(__file__).parent.parent / "reference" / "profile.json"
        if not ref_profile.exists():
            pytest.skip("reference/profile.json not found")

        manager = SessionManager(db_path=":memory:")
        session = manager.create_session(
            binary_path=Path("/bin/true"),
            args=[],
            working_directory=Path.cwd(),
            auto_approve=True,
        )
        run = Run(
            id=1,
            started_at=datetime.now(),
            duration_s=1.0,
            sample_count=100,
            profile_path=ref_profile,
            status=RunStatus.success,
            exit_code=0,
            stdout="",
            stderr="",
        )
        manager.add_run(session.id, run)

        with mock.patch.object(server, "_get_manager", return_value=manager):
            result = await server.get_callees(
                session_id=session.id,
                function_name="nonexistent_function_xyz",
            )

        assert "error" in result
        assert "No function matching" in result["error"]


class TestCompareRunsTool:
    @pytest.mark.asyncio
    async def test_compare_runs_detects_improvement(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_a_path = Path(tmpdir) / "a.json"
            profile_b_path = Path(tmpdir) / "b.json"

            profile_a = {
                "meta": {"interval": 1.0},
                "threads": [
                    {
                        "isMainThread": True,
                        "samples": {
                            "stack": [0, 0, 0],
                            "timeDeltas": [100, 100, 100],
                            "length": 3,
                        },
                        "frameTable": {
                            "func": [0, 1],
                            "inlineDepth": [0, 0],
                            "length": 2,
                        },
                        "funcTable": {"name": [0, 1]},
                        "stackTable": {
                            "frame": [0, 1],
                            "prefix": [None, 0],
                            "length": 2,
                        },
                        "stringArray": ["func_a", "func_b"],
                    }
                ],
            }
            profile_b = {
                "meta": {"interval": 1.0},
                "threads": [
                    {
                        "isMainThread": True,
                        "samples": {
                            "stack": [1, 1, 1],
                            "timeDeltas": [100, 100, 100],
                            "length": 3,
                        },
                        "frameTable": {
                            "func": [0, 1],
                            "inlineDepth": [0, 0],
                            "length": 2,
                        },
                        "funcTable": {"name": [0, 1]},
                        "stackTable": {
                            "frame": [0, 1],
                            "prefix": [None, 0],
                            "length": 2,
                        },
                        "stringArray": ["func_a", "func_b"],
                    }
                ],
            }

            profile_a_path.write_text(json.dumps(profile_a))
            profile_b_path.write_text(json.dumps(profile_b))

            manager = SessionManager(db_path=":memory:")
            session = manager.create_session(
                binary_path=Path("/bin/true"),
                args=[],
                working_directory=Path.cwd(),
                auto_approve=True,
            )
            run_a = Run(
                id=1,
                started_at=datetime.now(),
                duration_s=1.0,
                sample_count=3,
                profile_path=profile_a_path,
                status=RunStatus.success,
                exit_code=0,
                stdout="",
                stderr="",
            )
            run_b = Run(
                id=2,
                started_at=datetime.now(),
                duration_s=1.0,
                sample_count=3,
                profile_path=profile_b_path,
                status=RunStatus.success,
                exit_code=0,
                stdout="",
                stderr="",
            )
            manager.add_run(session.id, run_a)
            manager.add_run(session.id, run_b)

            with mock.patch.object(server, "_get_manager", return_value=manager):
                result = await server.compare_runs(
                    session_id=session.id,
                    run_id_a=1,
                    run_id_b=2,
                )

            assert "error" not in result
            assert "improved" in result
            assert "regressed" in result
            assert "new_hotspots" in result
            assert "resolved" in result
            assert result["sample_count_a"] == 3
            assert result["sample_count_b"] == 3

    @pytest.mark.asyncio
    async def test_compare_runs_invalid_run_id(self) -> None:
        manager = SessionManager(db_path=":memory:")
        session = manager.create_session(
            binary_path=Path("/bin/true"),
            args=[],
            working_directory=Path.cwd(),
            auto_approve=True,
        )

        with mock.patch.object(server, "_get_manager", return_value=manager):
            result = await server.compare_runs(
                session_id=session.id,
                run_id_a=1,
                run_id_b=2,
            )

        assert "error" in result


class TestProfileParseErrors:
    @pytest.mark.asyncio
    async def test_get_run_summary_profile_parse_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            malformed_profile = Path(tmpdir) / "malformed.json"
            malformed_profile.write_text("not valid json{")

            manager = SessionManager(db_path=":memory:")
            session = manager.create_session(
                binary_path=Path("/bin/true"),
                args=[],
                working_directory=Path.cwd(),
                auto_approve=True,
            )
            run = Run(
                id=1,
                started_at=datetime.now(),
                duration_s=1.0,
                sample_count=100,
                profile_path=malformed_profile,
                status=RunStatus.success,
                exit_code=0,
                stdout="",
                stderr="",
            )
            manager.add_run(session.id, run)

            with mock.patch.object(server, "_get_manager", return_value=manager):
                result = await server.get_run_summary(session_id=session.id)

            assert "error" in result
            assert "Failed to parse profile" in result["error"]

    @pytest.mark.asyncio
    async def test_get_hot_functions_profile_parse_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            malformed_profile = Path(tmpdir) / "malformed.json"
            malformed_profile.write_text("not valid json{")

            manager = SessionManager(db_path=":memory:")
            session = manager.create_session(
                binary_path=Path("/bin/true"),
                args=[],
                working_directory=Path.cwd(),
                auto_approve=True,
            )
            run = Run(
                id=1,
                started_at=datetime.now(),
                duration_s=1.0,
                sample_count=100,
                profile_path=malformed_profile,
                status=RunStatus.success,
                exit_code=0,
                stdout="",
                stderr="",
            )
            manager.add_run(session.id, run)

            with mock.patch.object(server, "_get_manager", return_value=manager):
                result = await server.get_hot_functions(session_id=session.id)

            assert isinstance(result, dict)
            assert "error" in result
            assert "Failed to parse profile" in result["error"]

    @pytest.mark.asyncio
    async def test_get_callers_profile_parse_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            malformed_profile = Path(tmpdir) / "malformed.json"
            malformed_profile.write_text("not valid json{")

            manager = SessionManager(db_path=":memory:")
            session = manager.create_session(
                binary_path=Path("/bin/true"),
                args=[],
                working_directory=Path.cwd(),
                auto_approve=True,
            )
            run = Run(
                id=1,
                started_at=datetime.now(),
                duration_s=1.0,
                sample_count=100,
                profile_path=malformed_profile,
                status=RunStatus.success,
                exit_code=0,
                stdout="",
                stderr="",
            )
            manager.add_run(session.id, run)

            with mock.patch.object(server, "_get_manager", return_value=manager):
                result = await server.get_callers(
                    session_id=session.id,
                    function_name="some_func",
                )

            assert "error" in result
            assert "Failed to parse profile" in result["error"]

    @pytest.mark.asyncio
    async def test_get_callees_profile_parse_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            malformed_profile = Path(tmpdir) / "malformed.json"
            malformed_profile.write_text("not valid json{")

            manager = SessionManager(db_path=":memory:")
            session = manager.create_session(
                binary_path=Path("/bin/true"),
                args=[],
                working_directory=Path.cwd(),
                auto_approve=True,
            )
            run = Run(
                id=1,
                started_at=datetime.now(),
                duration_s=1.0,
                sample_count=100,
                profile_path=malformed_profile,
                status=RunStatus.success,
                exit_code=0,
                stdout="",
                stderr="",
            )
            manager.add_run(session.id, run)

            with mock.patch.object(server, "_get_manager", return_value=manager):
                result = await server.get_callees(
                    session_id=session.id,
                    function_name="some_func",
                )

            assert "error" in result
            assert "Failed to parse profile" in result["error"]

    @pytest.mark.asyncio
    async def test_compare_runs_profile_parse_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            malformed_profile = Path(tmpdir) / "malformed.json"
            malformed_profile.write_text("not valid json{")

            manager = SessionManager(db_path=":memory:")
            session = manager.create_session(
                binary_path=Path("/bin/true"),
                args=[],
                working_directory=Path.cwd(),
                auto_approve=True,
            )
            run = Run(
                id=1,
                started_at=datetime.now(),
                duration_s=1.0,
                sample_count=100,
                profile_path=malformed_profile,
                status=RunStatus.success,
                exit_code=0,
                stdout="",
                stderr="",
            )
            manager.add_run(session.id, run)

            with mock.patch.object(server, "_get_manager", return_value=manager):
                result = await server.compare_runs(
                    session_id=session.id,
                    run_id_a=1,
                    run_id_b=1,
                )

            assert "error" in result
            assert "Failed to parse profile" in result["error"]

    @pytest.mark.asyncio
    async def test_profile_file_not_found(self) -> None:
        manager = SessionManager(db_path=":memory:")
        session = manager.create_session(
            binary_path=Path("/bin/true"),
            args=[],
            working_directory=Path.cwd(),
            auto_approve=True,
        )
        run = Run(
            id=1,
            started_at=datetime.now(),
            duration_s=1.0,
            sample_count=100,
            profile_path=Path("/nonexistent/profile.json"),
            status=RunStatus.success,
            exit_code=0,
            stdout="",
            stderr="",
        )
        manager.add_run(session.id, run)

        with mock.patch.object(server, "_get_manager", return_value=manager):
            result = await server.get_run_summary(session_id=session.id)

        assert "error" in result
        assert "Profile file not found" in result["error"]


class TestRunNotFound:
    @pytest.mark.asyncio
    async def test_get_run_summary_run_not_found(self) -> None:
        ref_profile = Path(__file__).parent.parent / "reference" / "profile.json"
        if not ref_profile.exists():
            pytest.skip("reference/profile.json not found")

        manager = SessionManager(db_path=":memory:")
        session = manager.create_session(
            binary_path=Path("/bin/true"),
            args=[],
            working_directory=Path.cwd(),
            auto_approve=True,
        )
        run = Run(
            id=1,
            started_at=datetime.now(),
            duration_s=1.0,
            sample_count=100,
            profile_path=ref_profile,
            status=RunStatus.success,
            exit_code=0,
            stdout="",
            stderr="",
        )
        manager.add_run(session.id, run)

        with mock.patch.object(server, "_get_manager", return_value=manager):
            result = await server.get_run_summary(session_id=session.id, run_id=999)

        assert "error" in result
        assert "Run '999' not found" in result["error"]
        assert session.id in result["error"]

    @pytest.mark.asyncio
    async def test_get_hot_functions_run_not_found(self) -> None:
        ref_profile = Path(__file__).parent.parent / "reference" / "profile.json"
        if not ref_profile.exists():
            pytest.skip("reference/profile.json not found")

        manager = SessionManager(db_path=":memory:")
        session = manager.create_session(
            binary_path=Path("/bin/true"),
            args=[],
            working_directory=Path.cwd(),
            auto_approve=True,
        )
        run = Run(
            id=1,
            started_at=datetime.now(),
            duration_s=1.0,
            sample_count=100,
            profile_path=ref_profile,
            status=RunStatus.success,
            exit_code=0,
            stdout="",
            stderr="",
        )
        manager.add_run(session.id, run)

        with mock.patch.object(server, "_get_manager", return_value=manager):
            result = await server.get_hot_functions(session_id=session.id, run_id=999)

        assert isinstance(result, dict)
        assert "error" in result
        assert "Run '999' not found" in result["error"]


class TestSetupScriptFailure:
    @pytest.mark.asyncio
    async def test_setup_script_non_zero_exit_returns_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            binary = Path(tmpdir) / "test_binary"
            binary.write_text("#!/bin/bash\necho hello\n")
            os.chmod(binary, 0o755)

            setup_script = Path(tmpdir) / "setup.sh"
            setup_script.write_text(
                "#!/bin/bash\necho 'setup stdout'; echo 'setup stderr' >&2; exit 42\n"
            )
            os.chmod(setup_script, 0o755)

            manager = SessionManager(db_path=":memory:")
            session = manager.create_session(
                binary_path=binary,
                args=[],
                setup_script_path=setup_script,
                working_directory=Path(tmpdir),
                auto_approve=True,
            )

            with (
                mock.patch.object(server, "_get_manager", return_value=manager),
                mock.patch.object(server, "check_perf_paranoid", return_value=(True, 1)),
            ):
                result = await server.run(session_id=session.id)

            assert "error" in result
            assert "Setup script failed" in result["error"]
            assert "exit code 42" in result["error"]
            assert result["run_id"] == 0
            assert result["status"] == "failed"
            assert "setup stdout" in result["stdout"]
            assert "setup stderr" in result["stderr"]
