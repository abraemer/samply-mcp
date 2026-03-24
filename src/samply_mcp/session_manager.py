"""Session lifecycle, persistence, and run queue management."""

import asyncio
import json
import random
import sqlite3
import string
from datetime import datetime, timedelta
from pathlib import Path

from samply_mcp.session import Run, RunStatus, Session, SessionState


class SessionManager:
    def __init__(self, db_path: Path | str | None = None):
        self._sessions: dict[str, Session] = {}

        if db_path is None:
            data_dir = Path.home() / ".local" / "share" / "samply-mcp"
            data_dir.mkdir(parents=True, exist_ok=True)
            profiles_dir = data_dir / "profiles"
            profiles_dir.mkdir(parents=True, exist_ok=True)
            db_path = str(data_dir / "sessions.db")

        if isinstance(db_path, Path):
            db_path = str(db_path)

        self._db_path = db_path
        self._is_memory_db = db_path == ":memory:"
        self._connection: sqlite3.Connection | None = None

        self._run_lock = asyncio.Lock()
        self._running_session_id: str | None = None

        self._create_tables()
        self._load_sessions()

    def _get_connection(self) -> sqlite3.Connection:
        if self._is_memory_db:
            if self._connection is None:
                self._connection = sqlite3.connect(self._db_path)
            return self._connection
        return sqlite3.connect(self._db_path)

    def _create_tables(self) -> None:
        conn = self._get_connection()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                binary_path TEXT NOT NULL,
                args TEXT NOT NULL,
                setup_script_path TEXT,
                setup_script_snapshot TEXT,
                env TEXT,
                working_directory TEXT NOT NULL,
                command_hash TEXT NOT NULL,
                state TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER NOT NULL,
                session_id TEXT NOT NULL,
                started_at TEXT NOT NULL,
                duration_s REAL NOT NULL,
                sample_count INTEGER NOT NULL,
                profile_path TEXT NOT NULL,
                status TEXT NOT NULL,
                exit_code INTEGER NOT NULL,
                stdout TEXT,
                stderr TEXT,
                log_path TEXT,
                PRIMARY KEY (session_id, id),
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            );
        """)
        if not self._is_memory_db:
            conn.close()

    def _save_session(self, session: Session) -> None:
        conn = self._get_connection()
        conn.execute(
            """
            INSERT OR REPLACE INTO sessions (
                id, binary_path, args, setup_script_path, setup_script_snapshot,
                env, working_directory, command_hash, state, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session.id,
                str(session.binary_path),
                json.dumps(session.args),
                str(session.setup_script_path) if session.setup_script_path else None,
                session.setup_script_snapshot,
                json.dumps(session.env) if session.env else None,
                str(session.working_directory),
                session.command_hash,
                session.state.value,
                session.created_at.isoformat(),
            ),
        )
        conn.commit()
        if not self._is_memory_db:
            conn.close()

    def _save_run(self, session_id: str, run: Run) -> None:
        conn = self._get_connection()
        conn.execute(
            """
            INSERT OR REPLACE INTO runs (
                id, session_id, started_at, duration_s, sample_count,
                profile_path, status, exit_code, stdout, stderr, log_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run.id,
                session_id,
                run.started_at.isoformat(),
                run.duration_s,
                run.sample_count,
                str(run.profile_path),
                run.status.value,
                run.exit_code,
                run.stdout,
                run.stderr,
                str(run.log_path) if run.log_path else None,
            ),
        )
        conn.commit()
        if not self._is_memory_db:
            conn.close()

    def _load_sessions(self) -> None:
        conn = self._get_connection()
        conn.row_factory = sqlite3.Row
        cursor = conn.execute("SELECT * FROM sessions")
        for row in cursor.fetchall():
            session = Session(
                id=row["id"],
                binary_path=Path(row["binary_path"]),
                args=json.loads(row["args"]),
                setup_script_path=Path(row["setup_script_path"])
                if row["setup_script_path"]
                else None,
                setup_script_snapshot=row["setup_script_snapshot"],
                env=json.loads(row["env"]) if row["env"] else None,
                working_directory=Path(row["working_directory"]),
                state=SessionState(row["state"]),
                runs=[],
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            self._sessions[session.id] = session

        cursor = conn.execute("SELECT * FROM runs ORDER BY session_id, id")
        for row in cursor.fetchall():
            run = Run(
                id=row["id"],
                started_at=datetime.fromisoformat(row["started_at"]),
                duration_s=row["duration_s"],
                sample_count=row["sample_count"],
                profile_path=Path(row["profile_path"]),
                status=RunStatus(row["status"]),
                exit_code=row["exit_code"],
                stdout=row["stdout"] or "",
                stderr=row["stderr"] or "",
                log_path=Path(row["log_path"]) if row["log_path"] else None,
            )
            session_id = row["session_id"]
            if session_id in self._sessions:
                self._sessions[session_id].runs.append(run)

        if not self._is_memory_db:
            conn.close()

    def _delete_session(self, session_id: str) -> None:
        conn = self._get_connection()
        conn.execute("DELETE FROM runs WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        conn.commit()
        if not self._is_memory_db:
            conn.close()
        if session_id in self._sessions:
            del self._sessions[session_id]

    def _generate_session_id(self) -> str:
        chars = string.ascii_lowercase + string.digits
        for _ in range(10):
            session_id = "".join(random.choices(chars, k=6))
            if session_id not in self._sessions:
                return session_id
        raise RuntimeError("Failed to generate unique session ID after 10 attempts")

    def create_session(
        self,
        binary_path: Path,
        args: list[str],
        working_directory: Path,
        setup_script_path: Path | None = None,
        env: dict | None = None,
        auto_approve: bool = False,
    ) -> Session:
        session_id = self._generate_session_id()

        setup_script_snapshot = None
        if setup_script_path is not None:
            setup_script_snapshot = setup_script_path.read_text()

        session = Session(
            id=session_id,
            binary_path=binary_path,
            args=args,
            setup_script_path=setup_script_path,
            setup_script_snapshot=setup_script_snapshot,
            env=env,
            working_directory=working_directory,
            state=SessionState.approved if auto_approve else SessionState.pending_approval,
            runs=[],
            created_at=datetime.now(),
        )

        self._save_session(session)
        self._sessions[session_id] = session
        return session

    def get_session(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def get_session_status(self, session_id: str) -> dict:
        session = self.get_session(session_id)
        if session is None:
            return {"error": f"Session '{session_id}' not found"}

        latest_run_id = None
        if session.runs:
            latest_run_id = session.runs[-1].id

        try:
            perf_paranoid = int(Path("/proc/sys/kernel/perf_event_paranoid").read_text().strip())
        except (OSError, ValueError):
            perf_paranoid = -1

        return {
            "session_id": session.id,
            "state": session.state.value,
            "binary_path": str(session.binary_path),
            "args": session.args,
            "run_count": len(session.runs),
            "latest_run_id": latest_run_id,
            "perf_paranoid_level": perf_paranoid,
            "perf_paranoid_ok": perf_paranoid <= 1,
        }

    def approve_session(self, session_id: str, hash: str) -> bool:
        session = self.get_session(session_id)
        if session is None:
            return False

        if session.state != SessionState.pending_approval:
            return False

        if session.command_hash != hash:
            return False

        session.state = SessionState.approved
        self._save_session(session)
        return True

    def reject_session(self, session_id: str) -> bool:
        session = self.get_session(session_id)
        if session is None:
            return False

        if session.state != SessionState.pending_approval:
            return False

        self._delete_session(session_id)
        return True

    def list_sessions(self) -> list[Session]:
        return list(self._sessions.values())

    def destroy_session(self, session_id: str, delete_profiles: bool = False) -> bool:
        session = self.get_session(session_id)
        if session is None:
            return False

        if self.is_session_running(session_id):
            return False

        if delete_profiles:
            for run in session.runs:
                if run.profile_path.exists():
                    run.profile_path.unlink()

        self._delete_session(session_id)
        return True

    async def acquire_run_lock(self, session_id: str) -> bool:
        """
        Acquire the global run lock for a session.
        Blocks if another run is in progress.
        Sets _running_session_id.
        """
        await self._run_lock.acquire()
        self._running_session_id = session_id
        return True

    async def release_run_lock(self):
        """
        Release the global run lock.
        Clears _running_session_id.
        """
        self._running_session_id = None
        self._run_lock.release()

    def is_session_running(self, session_id: str) -> bool:
        """Check if a session has a run in progress."""
        return self._running_session_id == session_id

    def get_next_run_id(self, session_id: str) -> int:
        """Get the next run ID for a session (1-indexed)."""
        session = self.get_session(session_id)
        if session is None:
            return 1
        return len(session.runs) + 1

    def get_profile_path(self, session_id: str, run_id: int) -> Path:
        """Get the profile file path for a run."""
        data_dir = Path.home() / ".local" / "share" / "samply-mcp"
        profiles_dir = data_dir / "profiles" / session_id
        profiles_dir.mkdir(parents=True, exist_ok=True)
        return profiles_dir / f"{run_id}.json"

    def add_run(self, session_id: str, run: Run) -> None:
        """Add a run to a session and persist it."""
        session = self.get_session(session_id)
        if session is None:
            return
        session.runs.append(run)
        self._save_run(session_id, run)

    def get_last_run_time(self, session_id: str) -> datetime | None:
        """Get the timestamp of the most recent run for a session."""
        session = self.get_session(session_id)
        if session is None or not session.runs:
            return None
        return session.runs[-1].started_at

    def gc_sessions(self, max_age_days: int = 7) -> list[str]:
        """
        Garbage collect sessions with no runs in the past max_age_days days.

        Args:
            max_age_days: Maximum age in days for a session without runs.

        Returns:
            List of destroyed session IDs.
        """
        cutoff = datetime.now() - timedelta(days=max_age_days)
        destroyed = []

        for session_id, session in list(self._sessions.items()):
            if self.is_session_running(session_id):
                continue

            last_run = self.get_last_run_time(session_id)
            if last_run is None:
                if session.created_at < cutoff:
                    self.destroy_session(session_id, delete_profiles=True)
                    destroyed.append(session_id)
            elif last_run < cutoff:
                self.destroy_session(session_id, delete_profiles=True)
                destroyed.append(session_id)

        return destroyed
