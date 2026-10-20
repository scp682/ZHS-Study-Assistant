"""Cancellable input and panel rendering without credentials or network access."""
import io
import re
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from terminal_ui import MenuBack, build_panel, display_width, is_terminal, prompt_field


class TerminalUiTests(unittest.TestCase):
    def test_back_command_cancels_line_input(self):
        with self.assertRaises(MenuBack):
            prompt_field("账号：", input_fn=lambda label: "/back")

    def test_escape_cancels_native_input(self):
        with redirect_stdout(io.StringIO()), patch("terminal_ui.is_terminal", return_value=True), \
                patch("msvcrt.getwch", return_value="\x1b"):
            with self.assertRaises(MenuBack):
                prompt_field("账号：")

    def test_native_password_is_masked(self):
        with redirect_stdout(io.StringIO()) as output, patch("terminal_ui.is_terminal", return_value=True), \
                patch("msvcrt.getwch", side_effect=list("fake-password\r")):
            self.assertEqual(prompt_field("密码：", hidden=True), "fake-password")
            self.assertNotIn("fake-password", output.getvalue())
            self.assertIn("*************", output.getvalue())

    def test_closed_input_is_not_treated_as_console(self):
        stream = io.StringIO()
        stream.close()
        self.assertFalse(is_terminal(stream))

    def test_submenu_borders_align_with_notes_and_masked_account(self):
        for width in (36, 76):
            lines = build_panel("账号登录", "上次账号：123****4567", ("更换账号", "返回上一级"),
                                notes=("Esc 取消",), width=width)
            for line in lines:
                plain = re.sub(r"\033\[[0-9;]*m", "", line)
                self.assertEqual(display_width(plain), width)


if __name__ == "__main__":
    unittest.main()
