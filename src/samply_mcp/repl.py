"""Terminal REPL frontend."""

import asyncio
import sys

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory

from samply_mcp.session import Session
from samply_mcp.session_manager import SessionManager


def notify_session_created(session: Session) -> None:
    """
    Print a notification to the terminal when a new session is created.

    This is called from the MCP server when create_session is invoked by an agent,
    so the human operator can review and approve/reject the session.
    """
    lines = [
        f"[samply-mcp] New session pending approval: {session.id}",
        f"  Binary:  {session.binary_path}",
    ]

    if session.args:
        lines.append(f"  Args:    {' '.join(session.args)}")
    else:
        lines.append("  Args:    (none)")

    if session.setup_script_path:
        lines.append(f"  Setup:   {session.setup_script_path}")

    if session.env:
        env_str = " ".join(f"{k}={v}" for k, v in session.env.items())
        lines.append(f"  Env:     {env_str}")

    if session.setup_script_snapshot:
        lines.append("")
        lines.append("  Setup script contents:")
        lines.append("  " + "\u2500" * 21)
        for script_line in session.setup_script_snapshot.splitlines():
            lines.append(f"  {script_line}")
        lines.append("  " + "\u2500" * 21)

    hash_display = session.command_hash[:8]
    lines.append("")
    lines.append(f"  Command hash: {hash_display}...")
    lines.append("")
    lines.append(f"  To approve: approve {session.id} {hash_display}")
    lines.append(f"  To reject:  reject {session.id}")

    print("\n".join(lines), file=sys.stderr)


class REPL:
    def __init__(self, manager: SessionManager):
        self.manager = manager
        self.session = PromptSession(history=FileHistory(".samply-mcp-history"))
        self.running = True

    async def run(self):
        """Main REPL loop."""
        while self.running:
            try:
                user_input = await self.session.prompt_async("samply-mcp> ")
                await self.handle_command(user_input.strip())
            except KeyboardInterrupt:
                print()
                continue
            except EOFError:
                self.running = False

    async def handle_command(self, line: str):
        """Parse and execute a command."""
        if not line:
            return

        parts = line.split()
        command = parts[0].lower()
        args = parts[1:]

        if command == "help":
            self.print_help()
        elif command == "quit":
            self.running = False
        elif command == "sessions":
            await self.cmd_sessions()
        elif command == "show":
            await self.cmd_show(args)
        elif command == "approve":
            await self.cmd_approve(args)
        elif command == "reject":
            await self.cmd_reject(args)
        elif command == "runs":
            await self.cmd_runs(args)
        elif command == "destroy":
            await self.cmd_destroy(args)
        elif command == "gc":
            await self.cmd_gc(args)
        else:
            print(f"Unknown command: {command}")

    def print_help(self):
        """Print available commands."""
        print("Available commands:")
        print("  sessions              list all sessions and their states")
        print("  show <session_id>     print full session details")
        print("  approve <id> <hash>   approve a pending session")
        print("  reject <session_id>   reject and destroy a pending session")
        print("  runs <session_id>     list all runs for a session")
        print("  destroy <session_id>  destroy a session (any state)")
        print(
            "  gc [days]             garbage collect sessions with no runs in N days (default: 7)"
        )
        print("  help                  show available commands")
        print("  quit                  stop the server")

    async def cmd_sessions(self):
        """List all sessions."""
        sessions = self.manager.list_sessions()
        if not sessions:
            print("No sessions.")
            return

        for s in sessions:
            print(f"  {s.id}: {s.state.value} - {s.binary_path} ({len(s.runs)} runs)")

    async def cmd_show(self, args: list[str]):
        """Show full session details."""
        if len(args) < 1:
            print("Usage: show <session_id>")
            return

        session_id = args[0]
        session = self.manager.get_session(session_id)
        if session is None:
            print(f"Session '{session_id}' not found.")
            return

        print(f"Session: {session.id}")
        print(f"  State:      {session.state.value}")
        print(f"  Binary:     {session.binary_path}")
        print(f"  Args:       {' '.join(session.args) if session.args else '(none)'}")
        print(f"  Working Dir: {session.working_directory}")
        if session.setup_script_path:
            print(f"  Setup:      {session.setup_script_path}")
        if session.env:
            print(f"  Env:        {session.env}")
        print(f"  Hash:       {session.command_hash[:16]}...")
        print(f"  Runs:       {len(session.runs)}")
        print(f"  Created:    {session.created_at.isoformat()}")

        if session.setup_script_snapshot:
            print("\n  Setup script contents:")
            print("  " + "-" * 40)
            for line in session.setup_script_snapshot.splitlines():
                print(f"  {line}")
            print("  " + "-" * 40)

    async def cmd_approve(self, args: list[str]):
        """Approve a pending session."""
        if len(args) < 2:
            print("Usage: approve <session_id> <hash>")
            return

        session_id = args[0]
        hash_value = args[1]

        session = self.manager.get_session(session_id)
        if session is None:
            print(f"Session '{session_id}' not found.")
            return

        if session.state.value != "pending_approval":
            print(f"Session '{session_id}' is not pending approval (state: {session.state.value}).")
            return

        if session.command_hash[:16] != hash_value and session.command_hash != hash_value:
            print(f"Hash mismatch. Expected: {session.command_hash[:16]}...")
            return

        success = self.manager.approve_session(session_id, session.command_hash)
        if success:
            print(f"Session '{session_id}' approved.")
        else:
            print(f"Failed to approve session '{session_id}'.")

    async def cmd_reject(self, args: list[str]):
        """Reject and destroy a pending session."""
        if len(args) < 1:
            print("Usage: reject <session_id>")
            return

        session_id = args[0]
        session = self.manager.get_session(session_id)
        if session is None:
            print(f"Session '{session_id}' not found.")
            return

        if session.state.value != "pending_approval":
            print(f"Session '{session_id}' is not pending approval (state: {session.state.value}).")
            return

        success = self.manager.reject_session(session_id)
        if success:
            print(f"Session '{session_id}' rejected and destroyed.")
        else:
            print(f"Failed to reject session '{session_id}'.")

    async def cmd_runs(self, args: list[str]):
        """List all runs for a session."""
        if len(args) < 1:
            print("Usage: runs <session_id>")
            return

        session_id = args[0]
        session = self.manager.get_session(session_id)
        if session is None:
            print(f"Session '{session_id}' not found.")
            return

        if not session.runs:
            print(f"No runs for session '{session_id}'.")
            return

        for run in session.runs:
            print(
                f"  Run {run.id}: {run.status.value} - "
                f"{run.duration_s:.2f}s, {run.sample_count} samples, "
                f"exit={run.exit_code}"
            )

    async def cmd_destroy(self, args: list[str]):
        """Destroy a session (any state)."""
        if len(args) < 1:
            print("Usage: destroy <session_id> [--delete-profiles]")
            return

        session_id = args[0]
        delete_profiles = "--delete-profiles" in args

        session = self.manager.get_session(session_id)
        if session is None:
            print(f"Session '{session_id}' not found.")
            return

        if self.manager.is_session_running(session_id):
            print(f"Cannot destroy session '{session_id}': a run is currently in progress.")
            return

        success = self.manager.destroy_session(session_id, delete_profiles=delete_profiles)
        if success:
            if delete_profiles:
                print(f"Session '{session_id}' destroyed (profiles deleted).")
            else:
                print(f"Session '{session_id}' destroyed (profiles kept).")
        else:
            print(f"Failed to destroy session '{session_id}'.")

    async def cmd_gc(self, args: list[str]):
        """Garbage collect old sessions."""
        max_age_days = 7
        if len(args) >= 1:
            try:
                max_age_days = int(args[0])
            except ValueError:
                print(f"Invalid number: {args[0]}")
                return

        print(f"Collecting sessions with no runs in the past {max_age_days} days...")
        destroyed = self.manager.gc_sessions(max_age_days=max_age_days)

        if destroyed:
            print(f"Destroyed {len(destroyed)} session(s): {', '.join(destroyed)}")
        else:
            print("No sessions to collect.")


def run_repl(manager: SessionManager) -> None:
    """Run the REPL in the current thread."""
    repl = REPL(manager)
    asyncio.run(repl.run())
