import json
import shlex
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List

from .utils import (
    GREEN,
    RESET,
    SHELL_EXECUTABLES,
    YELLOW,
    format_cmdline,
    get_session_path,
    is_terminal_emulator,
    leaf_cmdline,
    read_cmdline,
)


@dataclass
class WindowState:
    command: str
    class_name: str
    workspace_id: int
    is_floating: bool
    fullscreen: int
    focus_history_id: int
    at: List[int]
    size: List[int]
    match_command: str = ""
    leaf_command: str = ""

    @classmethod
    def from_dict(cls, data: dict):
        return cls(
            command=data.get("command", ""),
            class_name=data.get("class", ""),
            workspace_id=data["workspace"]["id"],
            is_floating=data.get("floating", False),
            fullscreen=data.get("fullscreen", 0),
            focus_history_id=data.get("focusHistoryID", 999),
            at=data.get("at", [0, 0]),
            size=data.get("size", [0, 0]),
            match_command=data.get("match_command", ""),
            leaf_command=data.get("leaf_command", ""),
        )


def save_session(name="last_session", overwrite=None):
    try:
        output = subprocess.check_output(["hyprctl", "clients", "-j"], text=True)
        data = json.loads(output)
    except subprocess.CalledProcessError:
        return

    windows = []
    for w in data:
        if (
            w.get("mapped")
            and w.get("initialClass")
            and "hypr-vault" not in w.get("title", "").lower()
        ):
            state = WindowState.from_dict(w)
            pid = w.get("pid", 0)
            launcher_argv = read_cmdline(pid)
            launcher_cmd = format_cmdline(launcher_argv)
            state.command = launcher_cmd
            state.match_command = launcher_cmd

            if state.class_name == "Docker Desktop":
                state.command = "/usr/local/bin/docker desktop start"
                state.match_command = ""
            elif state.class_name and is_terminal_emulator(state.class_name):
                leaf_argv = leaf_cmdline(pid)
                if leaf_argv:
                    leaf_exe = Path(leaf_argv[0]).name
                    leaf_cmd = format_cmdline(leaf_argv)
                    if leaf_exe not in SHELL_EXECUTABLES and leaf_cmd != launcher_cmd:
                        state.leaf_command = leaf_cmd
                        if not any(arg == "-e" for arg in launcher_argv):
                            state.command = f"{launcher_cmd} -e sh -lc {shlex.quote(leaf_cmd)}"

            windows.append(state)

    session_path = get_session_path(name)
    if Path(session_path).exists():
        if overwrite is False:
            print(f"{YELLOW}[!]{RESET} Cancelled.")
            return
        if overwrite is None:
            print(f"{YELLOW}[!]{RESET} Session already exists: {session_path}")
            print(f"{YELLOW}[!]{RESET} Do you want to overwrite it? (y/n)", end=" ")
            if input().lower() != "y":
                print(f"{YELLOW}[!]{RESET} Cancelled.")
                return

    with open(session_path, "w", encoding="utf-8") as f:
        json.dump([asdict(w) for w in windows], f, indent=4)
    print(f"{GREEN}[+]{RESET} Session saved to: {session_path}")
    subprocess.run(["notify-send", "HyprVault", f"Session saved: {name}"], check=False)


if __name__ == "__main__":
    save_session()
