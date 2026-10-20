"""Keyboard navigation tests without accounts or external requests."""
import io
import re
import unittest

from launcher_menu import SELECTED, build_panel, display_width, select_launcher_menu


class LauncherMenuTests(unittest.TestCase):
    def select(self, keys):
        output = io.StringIO()
        keys = iter(keys)
        result = select_launcher_menu(key_reader=lambda: next(keys), output=output)
        return result, output.getvalue()

    def test_enter_chooses_default_login(self):
        result, output = self.select(["enter"])
        self.assertEqual(result, 0)
        self.assertIn(SELECTED, output)
        self.assertIn("› 账号密码登录", output)
        self.assertTrue(output.endswith("\033[0m\033[?25h"))

    def test_down_selects_each_action(self):
        for steps in range(1, 5):
            with self.subTest(steps=steps):
                result, _ = self.select(["down"] * steps + ["enter"])
                self.assertEqual(result, steps)

    def test_arrows_wrap_at_both_ends(self):
        self.assertEqual(self.select(["up", "enter"])[0], 4)
        self.assertEqual(self.select(["down"] * 5 + ["enter"])[0], 0)

    def test_number_keys_do_not_select_or_move(self):
        self.assertEqual(self.select(["2", "3", "", "enter"])[0], 0)

    def test_cursor_restored_after_interrupt(self):
        output = io.StringIO()

        def interrupt():
            raise KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt):
            select_launcher_menu(key_reader=interrupt, output=output)
        self.assertTrue(output.getvalue().endswith("\033[0m\033[?25h"))

    def test_chinese_panel_borders_align_at_narrow_and_normal_widths(self):
        for width in (36, 52, 76):
            for compact in (False, True):
                with self.subTest(width=width, compact=compact):
                    for line in build_panel(2, width, compact):
                        plain = re.sub(r"\033\[[0-9;]*m", "", line)
                        self.assertEqual(display_width(plain), width)

    def test_compact_panel_fits_standard_console_height(self):
        self.assertLess(len(build_panel(0, 76, compact=True)), 24)


if __name__ == "__main__":
    unittest.main()
