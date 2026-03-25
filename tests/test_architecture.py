"""Architecture tests to enforce security boundaries.

These tests ensure that the MCP tool interface is properly constrained
and that the separation between user API (REPL) and agent API (MCP tools)
is maintained.
"""

import ast
import inspect
from pathlib import Path

from samply_mcp import server
from samply_mcp.session_manager import SessionManager


class TestMCPToolInterfaceSecurity:
    """
    Security tests to ensure the MCP tool interface is properly constrained.

    These tests enforce that:
    1. Only explicitly allowed tools are exposed to AI agents via MCP
    2. Session approval/rejection functions remain user-only (via REPL)
    3. Any changes to the tool interface require explicit review

    CRITICAL: The separation between user API (REPL) and agent API (MCP tools)
    is the core security boundary. Agents must NEVER be able to approve their
    own sessions, as this would allow them to run arbitrary code without human
    oversight.
    """

    # =========================================================================
    # WARNING: SECURITY-CRITICAL ALLOWLIST
    # =========================================================================
    # Any modification to this list requires a security audit to ensure:
    #
    # 1. No tool allows agents to approve/reject sessions
    # 2. No tool allows agents to bypass the approval flow
    # 3. No tool exposes sensitive operations that should be user-only
    #
    # The approval flow is the containment mechanism for AI agents. If an agent
    # can approve its own session, it can execute arbitrary code on the system
    # without human consent. This would completely undermine the security model.
    #
    # When adding a new tool, ask yourself:
    # - Could an agent use this to escape containment?
    # - Does this expose functionality that should require human approval?
    # - Is this consistent with the principle of least privilege?
    # =========================================================================
    ALLOWED_MCP_TOOLS: set[str] = {
        "create_session",
        "get_session_status",
        "destroy_session",
        "list_sessions",
        "run",
        "get_run_summary",
        "get_hot_functions",
        "get_callers",
        "get_callees",
        "compare_runs",
    }

    def test_exactly_these_tools_are_exposed_via_mcp(self) -> None:
        """Verify that only explicitly allowed tools are registered as MCP tools."""
        server_path = Path(__file__).parent.parent / "src" / "samply_mcp" / "server.py"
        source = server_path.read_text()
        tree = ast.parse(source)

        mcp_tools_found = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for decorator in node.decorator_list:
                    if isinstance(decorator, ast.Call):
                        if (
                            isinstance(decorator.func, ast.Attribute)
                            and decorator.func.attr == "tool"
                        ):
                            mcp_tools_found.add(node.name)
                    elif isinstance(decorator, ast.Attribute):
                        if decorator.attr == "tool":
                            mcp_tools_found.add(node.name)

        unexpected = mcp_tools_found - self.ALLOWED_MCP_TOOLS
        missing = self.ALLOWED_MCP_TOOLS - mcp_tools_found

        assert not unexpected, (
            f"Unexpected MCP tools found: {unexpected}. "
            f"If this is intentional, update ALLOWED_MCP_TOOLS after security review. "
            f"See the warning comment in this test file."
        )
        assert not missing, (
            f"Expected MCP tools not found: {missing}. "
            f"Update ALLOWED_MCP_TOOLS if tools were renamed/removed."
        )

    def test_approve_session_is_not_an_mcp_tool(self) -> None:
        """Verify approve_session is never exposed as an MCP tool."""
        server_path = Path(__file__).parent.parent / "src" / "samply_mcp" / "server.py"
        source = server_path.read_text()
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == "approve_session"
            ):
                for decorator in node.decorator_list:
                    if isinstance(decorator, ast.Call):
                        if (
                            isinstance(decorator.func, ast.Attribute)
                            and decorator.func.attr == "tool"
                        ):
                            raise AssertionError(
                                "SECURITY VIOLATION: approve_session is decorated with "
                                "@mcp.tool(). This would allow AI agents to approve their "
                                "own sessions, breaking the containment model. The "
                                "approve_session function must only be accessible via "
                                "the REPL (user interface)."
                            )
                    elif isinstance(decorator, ast.Attribute):
                        if decorator.attr == "tool":
                            raise AssertionError(
                                "SECURITY VIOLATION: approve_session is decorated with "
                                "@mcp.tool. This would allow AI agents to approve their "
                                "own sessions, breaking the containment model. The "
                                "approve_session function must only be accessible via "
                                "the REPL (user interface)."
                            )

    def test_reject_session_is_not_an_mcp_tool(self) -> None:
        """Verify reject_session is never exposed as an MCP tool."""
        server_path = Path(__file__).parent.parent / "src" / "samply_mcp" / "server.py"
        source = server_path.read_text()
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == "reject_session"
            ):
                for decorator in node.decorator_list:
                    if isinstance(decorator, ast.Call):
                        if (
                            isinstance(decorator.func, ast.Attribute)
                            and decorator.func.attr == "tool"
                        ):
                            raise AssertionError(
                                "SECURITY VIOLATION: reject_session is decorated with "
                                "@mcp.tool(). This would allow AI agents to reject "
                                "sessions without user consent."
                            )
                    elif isinstance(decorator, ast.Attribute):
                        if decorator.attr == "tool":
                            raise AssertionError(
                                "SECURITY VIOLATION: reject_session is decorated with "
                                "@mcp.tool. This would allow AI agents to reject "
                                "sessions without user consent."
                            )

    def test_approve_session_exists_in_session_manager(self) -> None:
        """Verify approve_session exists in SessionManager where it belongs."""
        assert hasattr(SessionManager, "approve_session"), (
            "approve_session method not found in SessionManager. "
            "This method must exist for the REPL to approve sessions. "
            "If it was renamed, update the REPL code accordingly."
        )

        assert callable(SessionManager.approve_session), "approve_session must be a callable method"

    def test_reject_session_exists_in_session_manager(self) -> None:
        """Verify reject_session exists in SessionManager where it belongs."""
        assert hasattr(SessionManager, "reject_session"), (
            "reject_session method not found in SessionManager. "
            "This method must exist for the REPL to reject sessions. "
            "If it was renamed, update the REPL code accordingly."
        )

        assert callable(SessionManager.reject_session), "reject_session must be a callable method"

    def test_approve_session_signature_includes_hash_verification(self) -> None:
        """Verify approve_session requires a hash parameter for security."""
        sig = inspect.signature(SessionManager.approve_session)
        params = list(sig.parameters.keys())

        assert "hash" in params, (
            "approve_session must have a 'hash' parameter to verify the session. "
            "This prevents approval of sessions without verifying the command hash."
        )

    def test_mcp_tools_do_not_expose_approve_reject_methods(self) -> None:
        """Verify that no MCP tool can directly approve or reject sessions."""
        for tool_name in self.ALLOWED_MCP_TOOLS:
            tool_func = getattr(server, tool_name, None)
            assert tool_func is not None, f"MCP tool '{tool_name}' not found in server module"

            source = inspect.getsource(tool_func)
            source_lower = source.lower()

            dangerous_patterns = [
                "approve_session",
                "reject_session",
                ".approve(",
                ".reject(",
            ]

            for pattern in dangerous_patterns:
                if pattern.lower() in source_lower:
                    raise AssertionError(
                        f"SECURITY VIOLATION: MCP tool '{tool_name}' appears to call "
                        f"'{pattern}'. MCP tools must never approve or reject sessions. "
                        f"Session approval must only happen via the REPL."
                    )
