"""Session and Run dataclasses."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from hashlib import sha256
from pathlib import Path


class SessionState(Enum):
    pending_approval = "pending_approval"
    approved = "approved"
    error = "error"


class RunStatus(Enum):
    success = "success"
    failed = "failed"
    timeout = "timeout"


class OutputMode(Enum):
    STDOUT = "STDOUT"
    DEVNULL = "DEVNULL"
    FILE = "FILE"


@dataclass
class Run:
    id: int
    started_at: datetime
    duration_s: float
    sample_count: int
    profile_path: Path
    status: RunStatus
    exit_code: int
    stdout: str
    stderr: str
    log_path: Path | None = None


def compute_command_hash(
    binary_path: Path,
    args: list[str],
    setup_script_snapshot: str | None,
    env: dict | None,
    working_directory: Path,
) -> str:
    hasher = sha256()
    hasher.update(str(binary_path).encode("utf-8"))
    hasher.update(b"\x00")
    for arg in args:
        hasher.update(arg.encode("utf-8"))
        hasher.update(b"\x00")
    if setup_script_snapshot is not None:
        hasher.update(setup_script_snapshot.encode("utf-8"))
    hasher.update(b"\x00")
    if env is not None:
        for key in sorted(env.keys()):
            hasher.update(key.encode("utf-8"))
            hasher.update(b"=")
            hasher.update(str(env[key]).encode("utf-8"))
            hasher.update(b"\x00")
    hasher.update(str(working_directory).encode("utf-8"))
    return hasher.hexdigest()


@dataclass
class Session:
    id: str
    binary_path: Path
    args: list[str]
    setup_script_path: Path | None
    setup_script_snapshot: str | None
    env: dict | None
    working_directory: Path
    state: SessionState
    runs: list[Run] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)

    @property
    def command_hash(self) -> str:
        return compute_command_hash(
            self.binary_path,
            self.args,
            self.setup_script_snapshot,
            self.env,
            self.working_directory,
        )
