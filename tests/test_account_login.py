"""Password login tests with fake credentials and no remote requests."""
import base64
import json
import queue
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.parse import quote, urlsplit

import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad

import account_login


class AccountLoginTests(unittest.TestCase):
    def setUp(self):
        self.session = Mock()
        self.session.cookies = requests.cookies.RequestsCookieJar()
        self.response = Mock()
        self.response.json.return_value = {"status": 1, "rt": {"pwd": "fake-once-password"}}
        self.session.post.return_value = self.response
        self.session.get.return_value = Mock()
        self.captcha = Mock(return_value="fake-human-validated-token")

    def decrypt(self, data):
        cipher = AES.new(account_login.LOGIN_KEY, AES.MODE_CBC, account_login.LOGIN_IV)
        return json.loads(unpad(cipher.decrypt(base64.b64decode(data)), 16).decode("utf-8"))

    def install_successful_handoff(self):
        def get(url, **kwargs):
            if url == "https://passport.zhihuishu.com/login":
                self.session.cookies.set("CASLOGC", quote(json.dumps({"uuid": "fake-user-id"})))
                self.session.cookies.set("CASTGC", "fake-cas-ticket")
            return Mock()
        self.session.get.side_effect = get

    def test_utf8_encryption_matches_browser_json_payload(self):
        payload = {"account": "测试用户", "password": "假的密码🔑", "validate": "fake-token"}
        encoded = account_login.encrypt_login_payload(payload)
        self.assertEqual(self.decrypt(encoded), payload)
        self.assertNotIn(payload["password"], encoded)

    def test_success_requires_captcha_then_cas_handoff(self):
        self.install_successful_handoff()
        cookies = account_login.login_with_password(
            self.session, "fake-account", "fake-password", self.captcha)
        self.captcha.assert_called_once_with()
        request = self.session.post.call_args
        self.assertEqual(request.args, (account_login.LOGIN_API,))
        self.assertEqual(self.decrypt(request.kwargs["data"]), {
            "account": "fake-account", "password": "fake-password",
            "validate": "fake-human-validated-token"})
        self.assertEqual(request.kwargs["headers"]["Origin"], "https://login.zhihuishu.com")
        self.assertEqual(self.session.get.call_args.kwargs["params"], {
            "pwd": "fake-once-password", "service": account_login.SERVICE})
        self.assertEqual(cookies["CASTGC"], "fake-cas-ticket")

    def test_wrong_password_is_not_retried(self):
        self.response.json.return_value = {"status": -2}
        with self.assertRaisesRegex(account_login.AccountLoginError, "账号或密码错误"):
            account_login.login_with_password(self.session, "fake-account", "wrong", self.captcha)
        self.session.post.assert_called_once()
        self.assertEqual(self.session.get.call_count, 1)

    def test_additional_verification_is_not_treated_as_success(self):
        for status in (-5, -9, -12, -13, -16, 2):
            with self.subTest(status=status):
                self.session.reset_mock()
                self.response.json.return_value = {"status": status, "rt": {"pwd": "unusable"}}
                with self.assertRaises(account_login.AccountLoginError):
                    account_login.login_with_password(self.session, "fake", "fake", self.captcha)
                self.assertEqual(self.session.get.call_count, 1)

    def test_missing_captcha_never_sends_credentials(self):
        self.captcha.return_value = ""
        with self.assertRaisesRegex(account_login.AccountLoginError, "人机验证"):
            account_login.login_with_password(self.session, "fake", "fake", self.captcha)
        self.session.post.assert_not_called()

    def test_http_success_without_cas_cookies_is_rejected(self):
        with self.assertRaisesRegex(account_login.AccountLoginError, "完整登录状态"):
            account_login.login_with_password(self.session, "fake", "fake", self.captcha)

    def test_missing_login_ticket_is_rejected(self):
        self.response.json.return_value = {"status": 1, "rt": {}}
        with self.assertRaisesRegex(account_login.AccountLoginError, "登录凭据"):
            account_login.login_with_password(self.session, "fake", "fake", self.captcha)
        self.assertEqual(self.session.get.call_count, 1)

    def test_browser_callback_accepts_only_local_origin_and_valid_token(self):
        urls, results = queue.Queue(), queue.Queue()

        def run():
            try:
                results.put(account_login.wait_for_login_captcha(
                    browser_factory=lambda url: account_login.webbrowser.open(url)))
            except BaseException as exc:
                results.put(exc)

        with patch("account_login.webbrowser.open", side_effect=lambda url: urls.put(url)), patch("builtins.print"):
            worker = threading.Thread(target=run, daemon=True)
            worker.start()
            url = urls.get(timeout=5)
            endpoint = url + "/complete"
            parsed = urlsplit(url)
            origin = f"{parsed.scheme}://{parsed.netloc}"
            local = requests.Session()
            local.trust_env = False
            try:
                page = local.get(url, timeout=3)
                self.assertEqual(page.status_code, 200)
                self.assertIn("new AbleCaptcha", page.text)
                self.assertIn("no-store", page.headers["Cache-Control"])
                rejected = local.post(endpoint, json={"validate": "fake"}, timeout=3,
                                      headers={"Origin": "https://example.invalid"})
                self.assertEqual(rejected.status_code, 403)
                invalid = local.post(endpoint, json={"validate": ""}, timeout=3,
                                     headers={"Origin": origin})
                self.assertEqual(invalid.status_code, 400)
            finally:
                response = local.post(endpoint, json={"validate": "fake-human-token"},
                                      headers={"Origin": origin}, timeout=3)
                local.close()
            self.assertEqual(response.status_code, 200)
            worker.join(timeout=5)
            self.assertFalse(worker.is_alive())
            self.assertEqual(results.get(timeout=1), "fake-human-token")


if __name__ == "__main__":
    unittest.main()
