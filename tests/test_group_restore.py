import asyncio
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hyprvault import load, save


def saved_window(
    command,
    workspace_id,
    *,
    address="",
    grouped=None,
    at=None,
    size=None,
    focus_history_id=999,
):
    window = {
        "command": command,
        "class_name": command,
        "workspace_id": workspace_id,
        "is_floating": False,
        "fullscreen": 0,
        "focus_history_id": focus_history_id,
        "at": at or [0, 0],
        "size": size or [100, 100],
        "match_command": "",
        "leaf_command": "",
    }
    if address:
        window["address"] = address
    if grouped is not None:
        window["grouped"] = grouped
    return window


class GroupCaptureTest(unittest.TestCase):
    def test_save_session_persists_group_membership_order(self):
        raw_clients = [
            {
                "address": "a1",
                "class": "term",
                "mapped": True,
                "initialClass": "term",
                "title": "t1",
                "workspace": {"id": 1},
                "floating": False,
                "fullscreen": 0,
                "focusHistoryID": 1,
                "at": [0, 0],
                "size": [500, 500],
                "pid": 1,
                "grouped": ["a1", "a2"],
            },
            {
                "address": "a2",
                "class": "term",
                "mapped": True,
                "initialClass": "term",
                "title": "t2",
                "workspace": {"id": 1},
                "floating": False,
                "fullscreen": 0,
                "focusHistoryID": 2,
                "at": [0, 0],
                "size": [500, 500],
                "pid": 2,
                "grouped": ["a1", "a2"],
            },
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            old_xdg = os.environ.get("XDG_CONFIG_HOME")
            os.environ["XDG_CONFIG_HOME"] = temp_dir
            try:
                with (
                    patch.object(save.subprocess, "check_output", return_value=json.dumps(raw_clients)),
                    patch.object(save.subprocess, "run", lambda *args, **kwargs: None),
                    patch("builtins.input", return_value="y"),
                ):
                    save.save_session("probe")
                session_path = Path(temp_dir) / "hyprvault" / "sessions" / "probe.json"
                saved = json.loads(session_path.read_text(encoding="utf-8"))
            finally:
                if old_xdg is None:
                    os.environ.pop("XDG_CONFIG_HOME", None)
                else:
                    os.environ["XDG_CONFIG_HOME"] = old_xdg

        self.assertEqual("a1", saved[0]["address"])
        self.assertEqual(["a1", "a2"], saved[0]["grouped"])
        self.assertEqual("a2", saved[1]["address"])
        self.assertEqual(["a1", "a2"], saved[1]["grouped"])


class GroupRestoreTest(unittest.TestCase):
    def run_restore(
        self,
        saved_windows,
        live_positions,
        live_grouped=None,
        unrestored_commands=None,
        late_restored_commands=None,
        failed_group_dispatchers=None,
        no_transition_dispatchers=None,
        focus_fail_addresses=None,
        live_fullscreen=None,
        reverse_reported_group_order=False,
    ):
        live_grouped = live_grouped or {}
        unrestored_commands = set(unrestored_commands or [])
        late_restored_commands = set(late_restored_commands or [])
        failed_group_dispatchers = set(failed_group_dispatchers or [])
        no_transition_dispatchers = set(no_transition_dispatchers or [])
        focus_fail_addresses = set(focus_fail_addresses or [])
        live_fullscreen = live_fullscreen or {}
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir) / "hyprvault" / "sessions"
            config_dir.mkdir(parents=True)
            (config_dir / "probe.json").write_text(json.dumps(saved_windows), encoding="utf-8")

            events = []
            restored_commands = []
            live_clients = {}
            active_address = None

            async def fake_init_hypr_config():
                return None

            async def fake_get_clients():
                return [dict(client) for client in live_clients.values()]

            async def fake_focus_window(address, timeout=1.0):
                nonlocal active_address
                active_address = address
                events.append(("focuswindow", address))
                return address not in focus_fail_addresses

            async def fake_dispatch(cmd_args, wait=True):
                nonlocal active_address
                events.append(tuple(cmd_args))
                if cmd_args and cmd_args[0] in failed_group_dispatchers:
                    return False
                if cmd_args and cmd_args[0] in no_transition_dispatchers:
                    return True
                if cmd_args and cmd_args[0] == "moveoutofgroup":
                    address = cmd_args[1].removeprefix("address:")
                    members = live_clients[address]["grouped"]
                    remaining = [member for member in members if member != address]
                    live_clients[address]["grouped"] = []
                    for member in remaining:
                        live_clients[member]["grouped"] = list(remaining)
                elif cmd_args and cmd_args[0] == "togglegroup":
                    grouped = live_clients[active_address]["grouped"]
                    live_clients[active_address]["grouped"] = (
                        [] if grouped == [active_address] else [active_address]
                    )
                elif cmd_args and cmd_args[0] in {"moveintoorcreategroup", "moveintogroup"}:
                    direction = cmd_args[1]
                    source = live_clients[active_address]
                    candidates = [
                        client
                        for address, client in live_clients.items()
                        if address != active_address
                        and load.direction_to_window(source, client) == direction
                    ]
                    target = min(
                        candidates,
                        key=lambda client: abs(client["at"][0] - source["at"][0])
                        + abs(client["at"][1] - source["at"][1]),
                    )
                    if target.get("fullscreen", 0) > 0:
                        return True
                    target_members = target["grouped"] or [target["address"]]
                    members = [*target_members, active_address]
                    reported_members = (
                        list(reversed(members))
                        if reverse_reported_group_order
                        else members
                    )
                    for address in members:
                        live_clients[address]["grouped"] = list(reported_members)
                return True

            async def fake_restore_window(sw, matchable_clients, used_addresses, force_spawn=False):
                restored_commands.append(sw["command"])
                if sw["command"] in unrestored_commands:
                    return None, None
                address = f"live-{sw['command']}"
                at = live_positions[sw["command"]]
                live_clients[address] = {
                    "address": address,
                    "at": at,
                    "size": [300, 300],
                    "grouped": list(live_grouped.get(sw["command"], [])),
                    "fullscreen": live_fullscreen.get(sw["command"], 0),
                }
                used_addresses.add(address)
                return address, sw.get("focus_history_id", 999)

            async def fake_reconcile_late_windows(saved_windows):
                restored = {}
                for sw in saved_windows:
                    if sw["command"] not in late_restored_commands:
                        continue
                    address = f"live-{sw['command']}"
                    live_clients[address] = {
                        "address": address,
                        "at": live_positions[sw["command"]],
                        "size": [300, 300],
                        "grouped": list(live_grouped.get(sw["command"], [])),
                        "fullscreen": live_fullscreen.get(sw["command"], 0),
                    }
                    restored[id(sw)] = address
                return restored

            async def fake_sleep(delay):
                return None

            old_xdg = os.environ.get("XDG_CONFIG_HOME")
            os.environ["XDG_CONFIG_HOME"] = temp_dir
            output = io.StringIO()
            try:
                with (
                    patch.object(load, "init_hypr_config", fake_init_hypr_config),
                    patch.object(load, "get_clients", fake_get_clients),
                    patch.object(load, "focus_window", fake_focus_window),
                    patch.object(load, "dispatch", fake_dispatch),
                    patch.object(load, "restore_window", fake_restore_window),
                    patch.object(load, "reconcile_late_windows", fake_reconcile_late_windows),
                    patch.object(load.asyncio, "sleep", fake_sleep),
                ):
                    with contextlib.redirect_stdout(output):
                        asyncio.run(load.restore_session("probe"))
            finally:
                if old_xdg is None:
                    os.environ.pop("XDG_CONFIG_HOME", None)
                else:
                    os.environ["XDG_CONFIG_HOME"] = old_xdg

            self.last_restore_output = output.getvalue()
            return events, restored_commands, live_clients

    def test_restores_multi_window_group_with_real_dispatcher_shapes(self):
        grouped = ["a1", "a2", "a3"]
        saved_windows = [
            saved_window(
                "alpha",
                1,
                address="a1",
                grouped=grouped,
                at=[0, 0],
                size=[500, 1000],
                focus_history_id=4,
            ),
            saved_window(
                "beta",
                1,
                address="a2",
                grouped=grouped,
                at=[0, 0],
                size=[500, 1000],
                focus_history_id=2,
            ),
            saved_window(
                "gamma",
                1,
                address="a3",
                grouped=grouped,
                at=[0, 0],
                size=[500, 1000],
                focus_history_id=5,
            ),
            saved_window(
                "standalone",
                1,
                at=[500, 0],
                size=[500, 1000],
                focus_history_id=0,
            ),
        ]

        events, restored_commands, _ = self.run_restore(
            saved_windows,
            {
                "alpha": [0, 0],
                "beta": [300, 0],
                "gamma": [600, 0],
                "standalone": [900, 0],
            },
        )

        self.assertEqual(["alpha", "standalone", "beta", "gamma"], restored_commands)
        group_moves = [
            event
            for event in events
            if event[0] in {"moveintoorcreategroup", "moveintogroup", "togglegroup"}
        ]
        self.assertEqual(
            [("moveintoorcreategroup", "l"), ("moveintogroup", "l")],
            group_moves,
        )

        first_join = events.index(("moveintoorcreategroup", "l"))
        second_join = events.index(("moveintogroup", "l"))
        self.assertEqual(("focuswindow", "live-beta"), events[first_join - 1])
        self.assertEqual(("focuswindow", "live-gamma"), events[second_join - 1])

        # Beta was the most recently focused tab inside this group when saved.
        self.assertIn(("focuswindow", "live-beta"), events[second_join + 1 :])
        self.assertEqual(("focuswindow", "address:live-standalone"), events[-2])
        self.assertEqual(("workspace", "1"), events[-1])

    def test_restores_single_window_group_with_bare_togglegroup(self):
        events, _, _ = self.run_restore(
            [
                saved_window(
                    "alpha",
                    1,
                    address="a1",
                    grouped=["a1"],
                    focus_history_id=1,
                )
            ],
            {"alpha": [0, 0]},
        )

        group_events = [event for event in events if event[0] == "togglegroup"]
        self.assertEqual([("togglegroup",)], group_events)
        toggle_index = events.index(("togglegroup",))
        self.assertEqual(("focuswindow", "live-alpha"), events[toggle_index - 1])

    def test_leaves_existing_same_member_group_intact(self):
        grouped = ["a1", "a2"]
        events, _, _ = self.run_restore(
            [
                saved_window("alpha", 1, address="a1", grouped=grouped, focus_history_id=2),
                saved_window("beta", 1, address="a2", grouped=grouped, focus_history_id=1),
            ],
            {"alpha": [0, 0], "beta": [300, 0]},
            {
                "alpha": ["live-beta", "live-alpha"],
                "beta": ["live-beta", "live-alpha"],
            },
        )

        group_events = [
            event
            for event in events
            if event[0]
            in {"moveoutofgroup", "moveintoorcreategroup", "moveintogroup", "togglegroup"}
        ]
        self.assertEqual([], group_events)
        self.assertIn(("focuswindow", "live-beta"), events)

    def test_skips_incomplete_group_instead_of_creating_singleton(self):
        grouped = ["a1", "a2"]
        events, _, _ = self.run_restore(
            [
                saved_window("alpha", 1, address="a1", grouped=grouped),
                saved_window("beta", 1, address="a2", grouped=grouped),
            ],
            {"alpha": [0, 0], "beta": [300, 0]},
            unrestored_commands={"beta"},
        )

        group_events = [
            event
            for event in events
            if event[0]
            in {"moveintoorcreategroup", "moveintogroup", "moveoutofgroup", "togglegroup"}
        ]
        self.assertEqual([], group_events)

    def test_failed_first_join_does_not_attempt_later_members(self):
        grouped = ["a1", "a2", "a3"]
        events, _, _ = self.run_restore(
            [
                saved_window("alpha", 1, address="a1", grouped=grouped),
                saved_window("beta", 1, address="a2", grouped=grouped),
                saved_window("gamma", 1, address="a3", grouped=grouped),
            ],
            {"alpha": [0, 0], "beta": [300, 0], "gamma": [600, 0]},
            failed_group_dispatchers={"moveintoorcreategroup"},
        )

        group_moves = [
            event
            for event in events
            if event[0] in {"moveintoorcreategroup", "moveintogroup"}
        ]
        self.assertEqual([("moveintoorcreategroup", "l")], group_moves)

    def test_rolls_back_group_created_with_unrelated_directional_target(self):
        grouped = ["a1", "a2"]
        events, _, live_clients = self.run_restore(
            [
                saved_window("alpha", 1, address="a1", grouped=grouped),
                saved_window("beta", 1, address="a2", grouped=grouped),
                saved_window("unrelated", 1),
            ],
            {
                "alpha": [0, 0],
                "unrelated": [300, 0],
                "beta": [600, 0],
            },
        )

        self.assertIn(("moveintoorcreategroup", "l"), events)
        self.assertTrue(
            all(not client["grouped"] for client in live_clients.values()),
            live_clients,
        )

    def test_warns_when_wrong_group_cannot_be_rolled_back(self):
        grouped = ["a1", "a2"]
        _, _, live_clients = self.run_restore(
            [
                saved_window("alpha", 1, address="a1", grouped=grouped),
                saved_window("beta", 1, address="a2", grouped=grouped),
                saved_window("unrelated", 1),
            ],
            {
                "alpha": [0, 0],
                "unrelated": [300, 0],
                "beta": [600, 0],
            },
            no_transition_dispatchers={"moveoutofgroup"},
        )

        self.assertTrue(live_clients["live-beta"]["grouped"])
        self.assertIn("rollback incomplete", self.last_restore_output)

    def test_accepts_correct_members_in_different_reported_order(self):
        grouped = ["a1", "a2"]
        events, _, live_clients = self.run_restore(
            [
                saved_window("alpha", 1, address="a1", grouped=grouped),
                saved_window("beta", 1, address="a2", grouped=grouped),
            ],
            {"alpha": [0, 0], "beta": [300, 0]},
            reverse_reported_group_order=True,
        )

        self.assertIn(("moveintoorcreategroup", "l"), events)
        self.assertNotIn(("moveoutofgroup", "address:live-beta"), events)
        self.assertEqual(
            {"live-alpha", "live-beta"},
            set(live_clients["live-alpha"]["grouped"]),
        )

    def test_restores_vertical_group(self):
        grouped = ["a1", "a2"]
        events, _, live_clients = self.run_restore(
            [
                saved_window("alpha", 1, address="a1", grouped=grouped),
                saved_window("beta", 1, address="a2", grouped=grouped),
            ],
            {"alpha": [0, 0], "beta": [0, 300]},
        )

        self.assertIn(("moveintoorcreategroup", "u"), events)
        self.assertEqual(
            ["live-alpha", "live-beta"],
            live_clients["live-alpha"]["grouped"],
        )

    def test_restores_multiple_independent_groups(self):
        first_group = ["a1", "a2"]
        second_group = ["b1", "b2"]
        events, _, live_clients = self.run_restore(
            [
                saved_window("alpha", 1, address="a1", grouped=first_group),
                saved_window("beta", 1, address="a2", grouped=first_group),
                saved_window("gamma", 1, address="b1", grouped=second_group),
                saved_window("delta", 1, address="b2", grouped=second_group),
            ],
            {
                "alpha": [0, 0],
                "beta": [300, 0],
                "gamma": [1000, 0],
                "delta": [1300, 0],
            },
        )

        self.assertEqual(2, events.count(("moveintoorcreategroup", "l")))
        self.assertEqual(
            ["live-alpha", "live-beta"],
            live_clients["live-alpha"]["grouped"],
        )
        self.assertEqual(
            ["live-gamma", "live-delta"],
            live_clients["live-gamma"]["grouped"],
        )

    def test_skips_cross_workspace_group(self):
        grouped = ["a1", "a2"]
        events, _, live_clients = self.run_restore(
            [
                saved_window("alpha", 1, address="a1", grouped=grouped),
                saved_window("beta", 2, address="a2", grouped=grouped),
            ],
            {"alpha": [0, 0], "beta": [300, 0]},
        )

        group_moves = [
            event
            for event in events
            if event[0] in {"moveintoorcreategroup", "moveintogroup", "togglegroup"}
        ]
        self.assertEqual([], group_moves)
        self.assertTrue(all(not client["grouped"] for client in live_clients.values()))

    def test_skips_conflicting_existing_group_without_mutation(self):
        grouped = ["a1", "a2"]
        events, _, live_clients = self.run_restore(
            [
                saved_window("alpha", 1, address="a1", grouped=grouped),
                saved_window("beta", 1, address="a2", grouped=grouped),
                saved_window("unrelated", 1),
            ],
            {"alpha": [0, 0], "beta": [300, 0], "unrelated": [600, 0]},
            {
                "alpha": ["live-alpha", "live-unrelated"],
                "unrelated": ["live-alpha", "live-unrelated"],
            },
        )

        group_moves = [
            event
            for event in events
            if event[0] in {"moveintoorcreategroup", "moveintogroup", "moveoutofgroup"}
        ]
        self.assertEqual([], group_moves)
        self.assertEqual(
            ["live-alpha", "live-unrelated"],
            live_clients["live-alpha"]["grouped"],
        )

    def test_skips_directionless_group_without_mutation(self):
        grouped = ["a1", "a2"]
        events, _, live_clients = self.run_restore(
            [
                saved_window("alpha", 1, address="a1", grouped=grouped),
                saved_window("beta", 1, address="a2", grouped=grouped),
            ],
            {"alpha": [0, 0], "beta": [0, 0]},
        )

        group_moves = [
            event
            for event in events
            if event[0] in {"moveintoorcreategroup", "moveintogroup", "moveoutofgroup"}
        ]
        self.assertEqual([], group_moves)
        self.assertTrue(all(not client["grouped"] for client in live_clients.values()))

    def test_successful_noop_join_stops_and_leaves_windows_ungrouped(self):
        grouped = ["a1", "a2", "a3"]
        events, _, live_clients = self.run_restore(
            [
                saved_window("alpha", 1, address="a1", grouped=grouped),
                saved_window("beta", 1, address="a2", grouped=grouped),
                saved_window("gamma", 1, address="a3", grouped=grouped),
            ],
            {"alpha": [0, 0], "beta": [300, 0], "gamma": [600, 0]},
            no_transition_dispatchers={"moveintoorcreategroup"},
        )

        group_moves = [
            event
            for event in events
            if event[0] in {"moveintoorcreategroup", "moveintogroup"}
        ]
        self.assertEqual([("moveintoorcreategroup", "l")], group_moves)
        self.assertTrue(all(not client["grouped"] for client in live_clients.values()))

    def test_later_noop_join_rolls_back_partial_group(self):
        grouped = ["a1", "a2", "a3"]
        events, _, live_clients = self.run_restore(
            [
                saved_window("alpha", 1, address="a1", grouped=grouped),
                saved_window("beta", 1, address="a2", grouped=grouped),
                saved_window("gamma", 1, address="a3", grouped=grouped),
            ],
            {"alpha": [0, 0], "beta": [300, 0], "gamma": [600, 0]},
            no_transition_dispatchers={"moveintogroup"},
        )

        group_moves = [
            event
            for event in events
            if event[0] in {"moveintoorcreategroup", "moveintogroup"}
        ]
        self.assertEqual(
            [("moveintoorcreategroup", "l"), ("moveintogroup", "l")],
            group_moves,
        )
        self.assertTrue(all(not client["grouped"] for client in live_clients.values()))

    def test_skips_fullscreen_group_before_dispatch(self):
        grouped = ["a1", "a2"]
        events, _, live_clients = self.run_restore(
            [
                saved_window("alpha", 1, address="a1", grouped=grouped),
                saved_window("beta", 1, address="a2", grouped=grouped),
            ],
            {"alpha": [0, 0], "beta": [300, 0]},
            live_fullscreen={"alpha": 1},
        )

        group_moves = [
            event
            for event in events
            if event[0] in {"moveintoorcreategroup", "moveintogroup", "moveoutofgroup"}
        ]
        self.assertEqual([], group_moves)
        self.assertTrue(all(not client["grouped"] for client in live_clients.values()))

    def test_focus_failure_stops_before_group_mutation(self):
        grouped = ["a1", "a2"]
        events, _, live_clients = self.run_restore(
            [
                saved_window("alpha", 1, address="a1", grouped=grouped),
                saved_window("beta", 1, address="a2", grouped=grouped),
            ],
            {"alpha": [0, 0], "beta": [300, 0]},
            focus_fail_addresses={"live-beta"},
        )

        self.assertNotIn(("moveintoorcreategroup", "l"), events)
        self.assertTrue(all(not client["grouped"] for client in live_clients.values()))

    def test_normalizes_self_exclusive_group_membership(self):
        events, _, live_clients = self.run_restore(
            [
                saved_window("alpha", 1, address="a1", grouped=["a2"]),
                saved_window("beta", 1, address="a2", grouped=["a1"]),
            ],
            {"alpha": [0, 0], "beta": [300, 0]},
        )

        group_moves = [
            event
            for event in events
            if event[0] in {"moveintoorcreategroup", "moveintogroup", "togglegroup"}
        ]
        self.assertEqual([("moveintoorcreategroup", "l")], group_moves)
        self.assertEqual(
            {"live-alpha", "live-beta"},
            set(live_clients["live-alpha"]["grouped"]),
        )

    def test_late_reconciled_member_can_join_group(self):
        grouped = ["a1", "a2"]
        events, _, live_clients = self.run_restore(
            [
                saved_window("alpha", 1, address="a1", grouped=grouped),
                saved_window("beta", 1, address="a2", grouped=grouped),
            ],
            {"alpha": [0, 0], "beta": [300, 0]},
            unrestored_commands={"beta"},
            late_restored_commands={"beta"},
        )

        self.assertIn(("moveintoorcreategroup", "l"), events)
        self.assertEqual(
            ["live-alpha", "live-beta"],
            live_clients["live-alpha"]["grouped"],
        )

    def test_legacy_session_emits_no_group_operations(self):
        events, _, _ = self.run_restore(
            [saved_window("alpha", 1)],
            {"alpha": [0, 0]},
        )

        group_events = [
            event
            for event in events
            if event[0]
            in {"moveintoorcreategroup", "moveintogroup", "moveoutofgroup", "togglegroup"}
        ]
        self.assertEqual([], group_events)


if __name__ == "__main__":
    unittest.main()
