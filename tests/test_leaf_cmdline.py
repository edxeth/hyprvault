import unittest
import os
import subprocess
import threading
from unittest.mock import patch

from hyprvault import utils


class LeafCmdlineTest(unittest.TestCase):
    """The leaf is the terminal's session program: the first non-shell child
    in the chain. Shells are unwrapped; a non-shell program that manages
    sub-processes (a multiplexer like herdr/tmux with panes) IS the leaf —
    its panes are window contents, not window identity."""

    def run_leaf(self, children_map, cmdline_map, root):
        with (
            patch.object(utils, "read_children", lambda pid: children_map.get(pid, [])),
            patch.object(utils, "read_cmdline", lambda pid: cmdline_map.get(pid, [])),
        ):
            return utils.leaf_cmdline(root)

    def test_multiplexer_is_the_leaf_not_its_first_pane(self):
        # ghostty -> zsh -> herdr -> [pane zsh -> nvim, pane zsh]
        result = self.run_leaf(
            {1: [2], 2: [3], 3: [4, 6], 4: [5], 6: []},
            {1: ["ghostty"], 2: ["zsh"], 3: ["herdr"], 4: ["zsh"], 5: ["nvim", "/tmp/x.md"], 6: ["zsh"]},
            1,
        )
        self.assertEqual(["herdr"], result)

    def test_single_command_terminal_returns_that_command(self):
        # ghostty -> zsh -> nvim
        result = self.run_leaf(
            {1: [2], 2: [3]},
            {1: ["ghostty"], 2: ["zsh"], 3: ["nvim", "/tmp/release-notes.md"]},
            1,
        )
        self.assertEqual(["nvim", "/tmp/release-notes.md"], result)

    def test_nested_shell_chain_unwraps_to_command(self):
        # ghostty -> zsh -> zsh -> btop
        result = self.run_leaf(
            {1: [2], 2: [3], 3: [4]},
            {1: ["ghostty"], 2: ["zsh"], 3: ["zsh"], 4: ["btop"]},
            1,
        )
        self.assertEqual(["btop"], result)

    def test_pure_shell_terminal_returns_shell(self):
        result = self.run_leaf(
            {1: [2]},
            {1: ["ghostty"], 2: ["zsh"]},
            1,
        )
        self.assertEqual(["zsh"], result)


class ReadChildrenThreadForkTest(unittest.TestCase):
    """Programs may fork their session process from a worker thread (e.g. a
    terminal's single-instance server). The main-thread children file stays
    empty, so children must be read across ALL threads of the pid."""

    def test_children_forked_from_worker_thread_are_visible(self):
        holder = {}
        spawned = threading.Event()
        release = threading.Event()

        def spawn_from_thread():
            holder["proc"] = subprocess.Popen(["sleep", "10"])
            spawned.set()
            release.wait(10)

        thread = threading.Thread(target=spawn_from_thread)
        thread.start()

        try:
            self.assertTrue(spawned.wait(5))
            # thread is still alive: the child is parented to the worker
            # thread, so the main-thread children file stays empty
            children = utils.read_children(os.getpid())
            self.assertIn(holder["proc"].pid, children)
        finally:
            release.set()
            thread.join()
            holder["proc"].terminate()
            holder["proc"].wait()


if __name__ == "__main__":
    unittest.main()
