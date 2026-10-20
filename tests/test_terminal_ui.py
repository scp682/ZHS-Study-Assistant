"""Cancellable input and panel rendering without credentials or network access."""
import io
import os
import re
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from terminal_ui import (MenuBack, build_panel, display_width, is_terminal, prompt_field,
                         render_panel, select_menu, show_result_page, wrap_text)


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

    def test_long_menu_scrolls_selected_item_into_view_and_fits_small_window(self):
        for columns, rows in [(52, 24), (80, 18), (100, 32)]:
            with self.subTest(size=(columns, rows)):
                output = io.StringIO()
                options = [f"第{i}章单元测试" for i in range(40)] + ["返回上一级"]
                frames = []

                def render(*args, **kwargs):
                    value = render_panel(*args, **kwargs)
                    frames.append(value)
                    return value

                keys = iter(["down"] * 40 + ["enter"])
                with patch("terminal_ui.shutil.get_terminal_size", return_value=os.terminal_size((columns, rows))), \
                     patch("terminal_ui.render_panel", side_effect=render):
                    selected = select_menu("章节测试", "选择测试", options,
                                           key_reader=lambda: next(keys), output=output, back_index=40)
                self.assertEqual(selected, 40)
                self.assertIn("› 返回上一级", frames[-1])
                for frame in frames:
                    self.assertLessEqual(len(frame.splitlines()), rows)

    def test_result_has_return_and_paginates_long_paths_without_data_loss(self):
        path = "C:\\课程目录\\" + "very-long-folder\\" * 12 + "questions.json"
        wrapped = wrap_text(path, 36)
        self.assertEqual("".join(wrapped), path)
        self.assertTrue(all(display_width(line) <= 36 for line in wrapped))
        output = io.StringIO()
        keys = iter(["enter", "down", "enter"])
        with patch("terminal_ui.shutil.get_terminal_size", return_value=os.terminal_size((80, 24))):
            show_result_page("章节测试", "测试列表", [f"测试{i}" for i in range(14)],
                             back_label="返回章节操作", key_reader=lambda: next(keys), output=output)
        self.assertIn("测试13", output.getvalue())
        self.assertIn("返回章节操作", output.getvalue())


if __name__ == "__main__":
    unittest.main()
