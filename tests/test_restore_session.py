import asyncio
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hyprvault import load


def saved_window(command, workspace_id, at=None, size=None):
    return {
        "command": command,
        "class_name": command,
        "workspace_id": workspace_id,
        "is_floating": False,
        "fullscreen": 0,
        "focus_history_id": 999,
        "at": at or [0, 0],
        "size": size or [100, 100],
        "match_command": "",
        "leaf_command": "",
    }


class RestoreSessionOrderTest(unittest.TestCase):
    def run_restore_with_session(self, saved_windows):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir) / "hyprvault" / "sessions"
            config_dir.mkdir(parents=True)
            (config_dir / "probe.json").write_text(json.dumps(saved_windows), encoding="utf-8")

            workspace_dispatches = []
            restored_commands = []

            async def fake_init_hypr_config():
                return None

            async def fake_get_clients():
                return []

            async def fake_dispatch(cmd_args, wait=True):
                if cmd_args and cmd_args[0] == "workspace":
                    workspace_dispatches.append(int(cmd_args[1]))

            async def fake_restore_window(sw, matchable_clients, used_addresses, force_spawn=False):
                restored_commands.append(sw["command"])
                addr = f"addr-{len(restored_commands)}"
                used_addresses.add(addr)
                return addr, sw.get("focus_history_id", 999)

            async def fake_reconcile_late_windows(saved_windows):
                return None

            async def fake_sleep(delay):
                return None

            old_xdg = os.environ.get("XDG_CONFIG_HOME")
            os.environ["XDG_CONFIG_HOME"] = temp_dir
            try:
                with (
                    patch.object(load, "init_hypr_config", fake_init_hypr_config),
                    patch.object(load, "get_clients", fake_get_clients),
                    patch.object(load, "dispatch", fake_dispatch),
                    patch.object(load, "restore_window", fake_restore_window),
                    patch.object(load, "reconcile_late_windows", fake_reconcile_late_windows),
                    patch.object(load.asyncio, "sleep", fake_sleep),
                ):
                    with contextlib.redirect_stdout(io.StringIO()):
                        asyncio.run(load.restore_session("probe"))
            finally:
                if old_xdg is None:
                    os.environ.pop("XDG_CONFIG_HOME", None)
                else:
                    os.environ["XDG_CONFIG_HOME"] = old_xdg

            return workspace_dispatches, restored_commands

    def test_restores_workspaces_in_numeric_order_not_json_first_seen_order(self):
        workspace_dispatches, restored_commands = self.run_restore_with_session(
            [
                saved_window("workspace-four", 4),
                saved_window("workspace-one", 1),
                saved_window("workspace-two", 2),
            ]
        )

        self.assertEqual([1, 2, 4], workspace_dispatches)
        self.assertEqual(["workspace-one", "workspace-two", "workspace-four"], restored_commands)

    def test_uses_saved_geometry_plan_within_each_workspace(self):
        workspace_dispatches, restored_commands = self.run_restore_with_session(
            [
                saved_window("right-window", 1, at=[500, 0], size=[500, 500]),
                saved_window("left-window", 1, at=[0, 0], size=[500, 500]),
            ]
        )

        self.assertEqual([1], workspace_dispatches)
        self.assertEqual(["left-window", "right-window"], restored_commands)


if __name__ == "__main__":
    unittest.main()
