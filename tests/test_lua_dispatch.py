"""Translation of legacy hyprctl dispatch tuples to Lua-manager dispatcher calls.

Hyprland 0.56.2+ with the Lua config manager parses the whole `hyprctl dispatch`
argument as Lua, so every legacy dispatcher name the loader emits must be
translated. These tests pin the exact expressions for each shape load.py uses.
"""

import unittest

from hyprvault.load import to_lua_dispatch, to_lua_exec


class ToLuaDispatch(unittest.TestCase):
    def test_focuswindow_by_address(self):
        self.assertEqual(
            to_lua_dispatch(["focuswindow", "address:0x1f"]),
            'hl.dsp.focus({ window = "address:0x1f" })',
        )

    def test_fullscreen_modes(self):
        self.assertEqual(
            to_lua_dispatch(["fullscreen", "0"]),
            'hl.dsp.window.fullscreen({ mode = "0", action = "set" })',
        )
        self.assertEqual(
            to_lua_dispatch(["fullscreen", "1"]),
            'hl.dsp.window.fullscreen({ mode = "1", action = "set" })',
        )
        # clients-JSON maximized (2) has no Lua mode; maximize is closest
        self.assertEqual(
            to_lua_dispatch(["fullscreen", "2"]),
            'hl.dsp.window.fullscreen({ mode = "1", action = "set" })',
        )

    def test_floating_state(self):
        self.assertEqual(
            to_lua_dispatch(["setfloating", "address:0x2"]),
            'hl.dsp.window.float({ action = "set", window = "address:0x2" })',
        )
        self.assertEqual(
            to_lua_dispatch(["settiled", "address:0x2"]),
            'hl.dsp.window.float({ action = "unset", window = "address:0x2" })',
        )

    def test_pixel_geometry_is_absolute(self):
        self.assertEqual(
            to_lua_dispatch(["resizewindowpixel", "exact 800 600,address:0x3"]),
            'hl.dsp.window.resize({ x = 800, y = 600, window = "address:0x3" })',
        )
        self.assertEqual(
            to_lua_dispatch(["movewindowpixel", "exact 14 42,address:0x3"]),
            'hl.dsp.window.move({ x = 14, y = 42, window = "address:0x3" })',
        )

    def test_silent_workspace_move(self):
        self.assertEqual(
            to_lua_dispatch(["movetoworkspacesilent", "3,address:0x4"]),
            'hl.dsp.window.move({ workspace = 3, window = "address:0x4", follow = false })',
        )

    def test_close_window(self):
        self.assertEqual(
            to_lua_dispatch(["closewindow", "address:0x5"]),
            'hl.dsp.window.close({ window = "address:0x5" })',
        )

    def test_workspace_switch(self):
        self.assertEqual(
            to_lua_dispatch(["workspace", "2"]),
            "hl.dsp.focus({ workspace = 2 })",
        )

    def test_layoutmsg_passes_through(self):
        self.assertEqual(
            to_lua_dispatch(["layoutmsg", "preselect l"]),
            'hl.dsp.layout("preselect l")',
        )

    def test_group_operations(self):
        self.assertEqual(
            to_lua_dispatch(["togglegroup"]),
            "hl.dsp.group.toggle()",
        )
        self.assertEqual(
            to_lua_dispatch(["moveoutofgroup", "address:0x6"]),
            'hl.dsp.window.move({ out_of_group = true, window = "address:0x6" })',
        )
        self.assertEqual(
            to_lua_dispatch(["moveintogroup", "u"]),
            'hl.dsp.window.move({ into_group = "up" })',
        )
        self.assertEqual(
            to_lua_dispatch(["moveintoorcreategroup", "r"]),
            'hl.dsp.window.move({ into_or_create_group = "right" })',
        )

    def test_unknown_dispatcher_returns_none(self):
        self.assertIsNone(to_lua_dispatch(["somethingnew", "x"]))


class ToLuaExec(unittest.TestCase):
    def test_tiled_spawn_rules(self):
        self.assertEqual(
            to_lua_exec("chromium", ws_id=2, is_floating=False, at=None, size=None),
            'hl.dsp.exec_cmd("chromium", { workspace = 2, no_initial_focus = true, tile = true })',
        )

    def test_floating_spawn_rules(self):
        self.assertEqual(
            to_lua_exec("term", ws_id=4, is_floating=True, at=[14, 42], size=[800, 600]),
            'hl.dsp.exec_cmd("term", { workspace = 4, no_initial_focus = true, '
            'float = true, move = "14 42", size = "800 600" })',
        )

    def test_command_with_embedded_quotes_is_escaped(self):
        cmd = "emacs --eval '(setenv \"TELEGA_PROFILE\" \"work\")'"
        expr = to_lua_exec(cmd, ws_id=1, is_floating=False, at=None, size=None)
        self.assertEqual(
            expr,
            'hl.dsp.exec_cmd("emacs --eval \'(setenv \\"TELEGA_PROFILE\\" \\"work\\")\'"'
            ", { workspace = 1, no_initial_focus = true, tile = true })",
        )


if __name__ == "__main__":
    unittest.main()
