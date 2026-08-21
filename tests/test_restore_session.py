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

    def test_restores_workspaces_in_numeric_order_then_returns_to_workspace_one(self):
        workspace_dispatches, restored_commands = self.run_restore_with_session(
            [
                saved_window("workspace-four", 4),
                saved_window("workspace-one", 1),
                saved_window("workspace-two", 2),
            ]
        )

        self.assertEqual([1, 2, 4, 1], workspace_dispatches)
        self.assertEqual(["workspace-one", "workspace-two", "workspace-four"], restored_commands)

    def test_uses_saved_geometry_plan_within_each_workspace(self):
        workspace_dispatches, restored_commands = self.run_restore_with_session(
            [
                saved_window("right-window", 1, at=[500, 0], size=[500, 500]),
                saved_window("left-window", 1, at=[0, 0], size=[500, 500]),
            ]
        )

        self.assertEqual([1, 1], workspace_dispatches)
        self.assertEqual(["left-window", "right-window"], restored_commands)


class ForceSpawnAdoptionTest(unittest.TestCase):
    """Preselect steps force-spawn, but a live window already at the exact
    saved placement must be adopted instead of closed and respawned."""

    def run_force_restore(self, saved, live_clients, launcher_map):
        events = []
        spawned = []

        async def fake_get_clients():
            return [dict(client) for client in live_clients.values()]

        async def fake_dispatch(cmd_args, wait=True):
            events.append(tuple(cmd_args))
            return True

        async def fake_sleep(delay):
            return None

        def fake_client_commands(client):
            return launcher_map.get(client["address"], ("", ""))

        class FakeProc:
            async def wait(self):
                return 0

            async def communicate(self):
                return (b"", b"")

        async def fake_exec(*args, **kwargs):
            spawned.append(args)
            return FakeProc()

        with (
            patch.object(load, "get_clients", fake_get_clients),
            patch.object(load, "dispatch", fake_dispatch),
            patch.object(load.asyncio, "sleep", fake_sleep),
            patch.object(load, "client_commands", fake_client_commands),
            patch.object(load.asyncio, "create_subprocess_exec", fake_exec),
        ):
            addr, fid = asyncio.run(
                load.restore_window(saved, [], set(), force_spawn=True)
            )

        return events, spawned, addr

    @staticmethod
    def saved_step_window(at):
        return {
            "command": "app-two",
            "class_name": "app-two",
            "workspace_id": 3,
            "is_floating": False,
            "fullscreen": 0,
            "focus_history_id": 7,
            "at": at,
            "size": [500, 500],
            "match_command": "app-two",
            "leaf_command": "",
        }

    @staticmethod
    def live_client(address, at):
        return {
            "address": address,
            "class": "app-two",
            "mapped": True,
            "workspace": {"id": 3},
            "floating": False,
            "fullscreen": 0,
            "at": at,
            "size": [500, 500],
        }

    def test_adopts_live_window_already_at_saved_placement(self):
        saved = self.saved_step_window([500, 0])
        live = self.live_client("live-app-two", [500, 0])

        events, spawned, addr = self.run_force_restore(
            saved, {"live-app-two": live}, {"live-app-two": ("app-two", "")}
        )

        self.assertEqual("live-app-two", addr)
        self.assertEqual([], [event for event in events if event[0] == "closewindow"])
        self.assertEqual([], spawned)

    def test_replays_live_window_at_wrong_placement(self):
        saved = self.saved_step_window([500, 0])
        live = self.live_client("live-app-two", [0, 0])

        events, spawned, addr = self.run_force_restore(
            saved, {"live-app-two": live}, {"live-app-two": ("app-two", "")}
        )

        self.assertIn(("closewindow", "address:live-app-two"), events)
        self.assertEqual(1, len(spawned))


if __name__ == "__main__":
    unittest.main()
