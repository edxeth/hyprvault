"""HyprVault utility functions and constants."""

import os
import shlex
import shutil
from pathlib import Path

# Colors
GREEN = "\033[92m"
BLUE = "\033[94m"
YELLOW = "\033[93m"
RED = "\033[91m"
BOLD = "\033[1m"
RESET = "\033[0m"


def get_config_dir() -> Path:
    """Get XDG-compliant config directory for hyprvault."""
    xdg_config = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
    config_dir = Path(xdg_config) / "hyprvault" / "sessions"
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir


def get_session_path(name: str) -> Path:
    """Get the full path for a session file, handling .json extension."""
    # Strip .json if user provided it
    if name.endswith(".json"):
        name = name[:-5]

    return get_config_dir() / f"{name}.json"


TERMINAL_EMULATORS = {"ghostty"}
SHELL_EXECUTABLES = {"bash", "dash", "fish", "nu", "sh", "tcsh", "xonsh", "zsh"}


def _strip_outer_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value



def _is_executable(candidate: str) -> bool:
    candidate = _strip_outer_quotes(candidate)
    if not candidate:
        return False

    path = Path(candidate)
    if path.is_file() and os.access(path, os.X_OK):
        return True

    return shutil.which(candidate) is not None



def normalize_argv(argv: list[str]) -> list[str]:
    argv = [_strip_outer_quotes(arg) for arg in argv if arg]
    if len(argv) != 1 or " " not in argv[0]:
        return argv

    blob = _strip_outer_quotes(argv[0])
    tokens = blob.split()
    for i in range(len(tokens), 0, -1):
        candidate = " ".join(tokens[:i])
        if _is_executable(candidate):
            return [_strip_outer_quotes(candidate), *[_strip_outer_quotes(token) for token in tokens[i:]]]

    return argv



def read_cmdline(pid: int) -> list[str]:
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            argv = [part.decode("utf-8") for part in f.read().split(b"\0") if part]
            return normalize_argv(argv)
    except Exception:
        return []



def format_cmdline(argv: list[str]) -> str:
    return shlex.join(normalize_argv(argv)) if argv else ""



def normalize_command_string(command: str) -> str:
    if not command:
        return ""

    try:
        argv = shlex.split(command)
    except Exception:
        argv = [command]

    return format_cmdline(argv)


def read_children(pid: int) -> list[int]:
    try:
        with open(f"/proc/{pid}/task/{pid}/children", "r", encoding="utf-8") as f:
            return [int(child) for child in f.read().split() if child.isdigit()]
    except Exception:
        return []


def leaf_cmdline(pid: int) -> list[str]:
    children = read_children(pid)
    if not children:
        return read_cmdline(pid)

    for child in children:
        child_cmd = leaf_cmdline(child)
        if child_cmd:
            exe = Path(child_cmd[0]).name
            if exe not in SHELL_EXECUTABLES:
                return child_cmd

    return leaf_cmdline(children[0])


def is_terminal_emulator(class_name: str) -> bool:
    normalized = class_name.lower()
    return normalized in TERMINAL_EMULATORS or normalized.rsplit(".", 1)[-1] in TERMINAL_EMULATORS


def list_sessions() -> list[str]:
    """List all saved session names."""
    config_dir = get_config_dir()
    sessions = []
    for f in config_dir.glob("*.json"):
        sessions.append(f.stem)
    return sorted(sessions)
