"""Remember/switch account checks with fake accounts and temporary files only."""
import ast
import json
import io
import runpy
import sys
import shutil
import types
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import quote

import requests
import utils
from login_profile import choose_login, load_profile, save_profile, write_json_atomic, _read_menu_key
from terminal_ui import BACK_CODE, MenuBack, SELECTED


ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def test_directory():
    # Windows Python 3.14 creates mode-0700 temp directories whose ACL excludes
    # this restricted process token. Use an ordinary inherited workspace ACL.
    folder = ROOT / ("login-test-" + uuid.uuid4().hex)
    folder.mkdir()
    try:
        yield folder
    finally:
        assert folder.resolve().parent == ROOT.resolve()
        shutil.rmtree(folder)


def cookies_for(uuid):
    jar = requests.cookies.RequestsCookieJar()
    jar.set("CASLOGC", quote(json.dumps({"uuid": uuid})))
    jar.set("CASTGC", "fake-ticket")
    return jar


class LoginProfileTests(unittest.TestCase):
    def setUp(self):
        self.menu_output = io.StringIO()
        stdout_patch = patch.object(sys, "stdout", self.menu_output)
        stdout_patch.start()
        self.addCleanup(stdout_patch.stop)
        self.saved = {"version": 1, "method": "password", "username": "fake-alice",
                      "password": "fake-old-password", "uuid": "fake-alice-id"}

    def test_password_is_saved_as_plaintext_and_read_back(self):
        with test_directory() as folder:
            path = Path(folder) / "login_profile.json"
            save_profile(path, "测试账号", "fake-password-密码", False, cookies_for("fake-id"))
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(raw["password"], "fake-password-密码")
            self.assertEqual(load_profile(path)["username"], "测试账号")

    def test_enter_reuses_saved_account_and_password_login_method(self):
        choice = choose_login(self.saved, "", "", True, key_reader=Mock(return_value="enter"))
        self.assertEqual(choice.username, "fake-alice")
        self.assertEqual(choice.password, "fake-old-password")
        self.assertFalse(choice.use_qr)
        self.assertTrue(choice.reuse_cookies)

    def test_switch_never_inherits_config_credentials_or_cookies(self):
        choice = choose_login(self.saved, "old-config-user", "old-config-password", False,
                              key_reader=iter(("down", "enter")).__next__)
        self.assertEqual((choice.username, choice.password), ("", ""))
        self.assertFalse(choice.reuse_cookies)

    def test_switch_keeps_previous_password_login_method(self):
        choice = choose_login(self.saved, "", "", True, key_reader=iter(("down", "enter")).__next__)
        self.assertFalse(choice.use_qr)

    def test_explicit_credentials_never_reuse_another_accounts_cookies(self):
        prompt = Mock()
        choice = choose_login(self.saved, "fake-bob", "new-password", False,
                              explicit_credentials=True, key_reader=prompt)
        self.assertEqual(choice.username, "fake-bob")
        self.assertFalse(choice.reuse_cookies)
        prompt.assert_not_called()

    def test_arrow_up_returns_to_previous_account_and_cursor_is_restored(self):
        choice = choose_login(self.saved, "", "", True,
                              key_reader=iter(("down", "up", "enter")).__next__)
        self.assertTrue(choice.reuse_cookies)
        self.assertIn(SELECTED, self.menu_output.getvalue())
        self.assertIn("› 使用上次登录账号登录", self.menu_output.getvalue())
        self.assertTrue(self.menu_output.getvalue().endswith("\033[0m\033[?25h"))

    def test_windows_extended_key_codes_are_decoded(self):
        with patch("msvcrt.getwch", side_effect=["\xe0", "P", "\x00", "H", "\r"]):
            self.assertEqual([_read_menu_key(), _read_menu_key(), _read_menu_key()],
                             ["down", "up", "enter"])

    def test_qr_profile_does_not_store_unrelated_password(self):
        with test_directory() as folder:
            path = Path(folder) / "login_profile.json"
            save_profile(path, "old-user", "old-password", True, cookies_for("new-qr-id"))
            saved = load_profile(path)
            self.assertEqual((saved["username"], saved["password"]), ("", ""))
            self.assertEqual(saved["uuid"], "new-qr-id")

    def test_failed_atomic_save_preserves_old_profile(self):
        with test_directory() as folder:
            path = Path(folder) / "login_profile.json"
            write_json_atomic(path, self.saved)
            with patch("login_profile.os.replace", side_effect=OSError("fake failure")):
                with self.assertRaises(OSError):
                    write_json_atomic(path, {"username": "fake-bob"})
            self.assertEqual(load_profile(path), self.saved)
            self.assertEqual([p.name for p in Path(folder).iterdir()], ["login_profile.json"])

    def run_main(self, answer, mismatched_cookie=False, fail_login=False, interactive=False,
                 exam_expired=False, fail_exam=False, use_qr=False):
        events = []
        def authenticate_exam(client):
            from chapter_tests import ChapterError
            attempts = sum(e[0] == "exam-auth" for e in events)
            events.append(("exam-auth", client))
            if fail_exam or (exam_expired and attempts == 0):
                raise ChapterError("fake expired CAS ticket")
            client.cookies.set("JSESSIONID", "verified-exam", domain="studentexam-api.zhihuishu.com")

        class FakeClient:
            def __init__(self, **kwargs):
                self._cookies = requests.cookies.RequestsCookieJar()
                events.append(("new-client",))

            @property
            def cookies(self):
                return self._cookies

            @cookies.setter
            def cookies(self, value):
                self._cookies = utils.list_to_cookie_jar(value)
                events.append(("load-cookies",))

            def getZhidaoList(self):
                events.append(("check-cookies",))
                return []

            def getHikeList(self): return []
            def getZhidaoAiList(self): return []

            def login(self, username=None, password=None, **kwargs):
                events.append(("login", username, password))
                if fail_login:
                    raise ValueError("fake invalid password")
                self._cookies = cookies_for("fake-bob-id" if username == "fake-bob" else "fake-alice-id")

            def fuckCourse(self, **kwargs):
                events.append(("video", self, kwargs))

        with test_directory() as folder:
            directory = Path(folder)
            module = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
            config = next(ast.literal_eval(n.value) for n in module.body
                          if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "DEFAULT_CONFIG" for t in n.targets))
            write_json_atomic(directory / "config.json", config)
            write_json_atomic(directory / "login_profile.json", self.saved)
            cookie_id = "another-account-id" if mismatched_cookie else "fake-alice-id"
            write_json_atomic(directory / "cookies.json", utils.cookie_jar_to_list(cookies_for(cookie_id)))
            fake_logger = types.ModuleType("logger")
            fake_logger.logger = Mock()
            fake_logger.logger.getLevel.return_value = "INFO"
            fake_module = types.ModuleType("fucker")
            fake_module.Fucker = FakeClient
            keys = ["up", "enter"] if answer == "back" else (["down", "enter"] if answer == "2" else ["enter"])
            with patch.dict(sys.modules, {"fucker": fake_module, "logger": fake_logger}), \
                 patch("utils.getConfigPath", return_value=str(directory / "config.json")), \
                 patch("utils.getRealPath", side_effect=lambda name: str(directory / Path(name).name)), \
                 patch.object(sys, "argv", ["main.py", "-q" if use_qr else "--password-login", "-c", "fake-course"] + (["--interactive-menu"] if interactive else [])), \
                 patch("login_profile._read_menu_key", side_effect=keys), \
                 patch("study_session.select_menu", side_effect=[1, 0, 1, 2]), \
                 patch("study_session.show_page"), patch("study_session.show_result_page"), \
                 patch("chapter_tests.run_chapter_cli", side_effect=lambda client, *a, **kw: events.append(("questions", client))), \
                 patch("chapter_tests.ChapterAPI.from_client", side_effect=lambda client: Mock(authenticate=lambda: authenticate_exam(client))), \
                 patch("builtins.input", return_value="fake-bob"), \
                 patch("getpass.getpass", return_value="fake-new-password"), patch("builtins.print"):
                if fail_login or fail_exam or answer == "back" or interactive:
                    with self.assertRaises(SystemExit) as stopped:
                        runpy.run_path(str(ROOT / "main.py"), run_name="__main__")
                    self.assertEqual(stopped.exception.code, BACK_CODE if answer == "back" or (interactive and not fail_login and not fail_exam) else 1)
                else:
                    runpy.run_path(str(ROOT / "main.py"), run_name="__main__")
            if interactive and not fail_exam and answer != "back":
                saved_cookies = utils.list_to_cookie_jar(json.loads((directory / "cookies.json").read_text(encoding="utf-8")))
                self.assertEqual(saved_cookies.get("JSESSIONID"), "verified-exam")
            return events, load_profile(directory / "login_profile.json")

    def test_main_reuses_session_without_asking_for_password_again(self):
        events, saved = self.run_main("1")
        self.assertIn(("load-cookies",), events)
        self.assertFalse(any(e[0] == "login" for e in events))
        self.assertEqual(saved["username"], "fake-alice")

    def test_interactive_features_share_one_fresh_login_and_client(self):
        events, _ = self.run_main("2", interactive=True)
        self.assertEqual(sum(e[0] == "login" for e in events), 1)
        feature_events = [e for e in events if e[0] in ("questions", "video")]
        self.assertEqual([e[0] for e in feature_events], ["questions", "video", "questions"])
        self.assertTrue(all(e[1] is feature_events[0][1] for e in feature_events))

    def test_interactive_features_reuse_cached_login(self):
        events, _ = self.run_main("1", interactive=True)
        self.assertFalse(any(e[0] == "login" for e in events))
        self.assertEqual(sum(e[0] == "questions" for e in events), 2)

    def test_expired_cas_is_renewed_before_entering_features(self):
        events, _ = self.run_main("1", interactive=True, exam_expired=True)
        self.assertEqual(sum(e[0] == "login" for e in events), 1)
        self.assertEqual(sum(e[0] == "exam-auth" for e in events), 2)
        self.assertLess(next(i for i, e in enumerate(events) if e[0] == "login"),
                        next(i for i, e in enumerate(events) if e[0] == "questions"))

    def test_qr_login_also_establishes_exam_login_before_features(self):
        events, _ = self.run_main("2", interactive=True, use_qr=True)
        self.assertEqual(sum(e[0] == "login" for e in events), 1)
        self.assertLess(next(i for i, e in enumerate(events) if e[0] == "exam-auth"),
                        next(i for i, e in enumerate(events) if e[0] == "questions"))

    def test_failed_exam_login_never_enters_features_or_replaces_profile(self):
        events, saved = self.run_main("2", interactive=True, fail_exam=True)
        self.assertFalse(any(e[0] in ("questions", "video") for e in events))
        self.assertEqual(saved, self.saved)

    def test_main_switches_account_and_saves_new_password_only_after_success(self):
        events, saved = self.run_main("2")
        self.assertNotIn(("load-cookies",), events)
        self.assertIn(("login", "fake-bob", "fake-new-password"), events)
        self.assertEqual((saved["username"], saved["password"], saved["uuid"]),
                         ("fake-bob", "fake-new-password", "fake-bob-id"))

    def test_main_rejects_cookies_from_another_account_and_logs_in_fresh(self):
        events, saved = self.run_main("1", mismatched_cookie=True)
        self.assertNotIn(("check-cookies",), events)
        self.assertEqual(events.count(("new-client",)), 2)
        self.assertIn(("login", "fake-alice", "fake-old-password"), events)

    def test_failed_new_login_preserves_the_previous_saved_account(self):
        events, saved = self.run_main("2", fail_login=True)
        self.assertEqual(saved, self.saved)

    def test_back_exits_before_creating_client_or_loading_cookies(self):
        events, saved = self.run_main("back")
        self.assertEqual(events, [])
        self.assertEqual(saved, self.saved)

    def test_escape_returns_without_selecting_saved_credentials(self):
        with self.assertRaises(MenuBack):
            choose_login(self.saved, "", "", False, key_reader=lambda: "back")


if __name__ == "__main__":
    unittest.main()
