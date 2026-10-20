"""Notification payload checks: never send a real external message."""
import json
import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

import requests
import push
from notification_settings import setup_feishu, check_feishu, test_feishu as send_feishu_test
from notification_settings import run_notification_page
from terminal_ui import BACK_CODE, MenuBack
from test_login_profile import test_directory


WEBHOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/fake-test-bot"


class FeishuPushTests(unittest.TestCase):
    def setUp(self):
        patcher = patch("push.requests.post")
        self.post = patcher.start()
        self.addCleanup(patcher.stop)
        response = Mock(status_code=200)
        response.json.return_value = {"code": 0}
        self.post.return_value = response

    def test_text_payload_and_success_response(self):
        push.feishupusher("智慧树学习提醒", "需要人机验证", WEBHOOK)
        call = self.post.call_args
        self.assertEqual(call.args, (WEBHOOK,))
        self.assertEqual(call.kwargs["json"], {
            "msg_type": "text", "content": {"text": "智慧树学习提醒\n需要人机验证"}})
        self.assertEqual(call.kwargs["timeout"], 10)
        self.assertFalse(call.kwargs["allow_redirects"])

    def test_signature_matches_independent_node_crypto_reference(self):
        with patch("push.time.time", return_value=1700000000):
            push.feishupusher("智慧树", "验证提醒", WEBHOOK, "fake-secret")
        payload = self.post.call_args.kwargs["json"]
        self.assertEqual(payload["timestamp"], "1700000000")
        self.assertEqual(payload["sign"], "Kd/wAnoObTBJ5Vz2UggmZWr221hCbtnpx7xUnVkIGMU=")
        self.assertNotIn("fake-secret", json.dumps(payload))

    def test_application_error_is_not_reported_as_delivery_success(self):
        self.post.return_value.json.return_value = {"code": 19021}
        with self.assertRaisesRegex(push.NotificationError, "19021"):
            push.feishupusher("智慧树", "验证提醒", WEBHOOK)

    def test_network_errors_do_not_expose_secret_webhook(self):
        self.post.side_effect = requests.Timeout(WEBHOOK)
        with self.assertRaises(push.NotificationError) as caught:
            push.feishupusher("智慧树", "验证提醒", WEBHOOK)
        self.assertNotIn(WEBHOOK, str(caught.exception))

    def test_unexpected_host_is_rejected_before_sending(self):
        for url in ("http://open.feishu.cn/open-apis/bot/v2/hook/fake", "https://example.invalid/hook",
                    "https://open.feishu.cn/open-apis/bot/v2/hook/"):
            with self.subTest(url=url), self.assertRaises(push.NotificationError):
                push.feishupusher("智慧树", "验证提醒", url)
        self.post.assert_not_called()

    def test_setup_preserves_existing_account_and_unrelated_config(self):
        with test_directory() as folder, patch("builtins.print"):
            path = folder / "config.json"
            original = {"username": "fake-user", "password": "fake-password", "ai": {"enabled": False}}
            path.write_text(json.dumps(original), encoding="utf-8")
            setup_feishu(path, input_fn=Mock(side_effect=[WEBHOOK, "fake-secret"]))
            saved = json.loads(path.read_text(encoding="utf-8"))
            for key, value in original.items():
                self.assertEqual(saved[key], value)
            self.assertEqual(saved["feishu"], {"enable": True, "webhook": WEBHOOK, "secret": "fake-secret"})
            self.assertTrue(check_feishu(path))
            self.post.assert_not_called()

    def test_disabled_channel_does_not_send_test_message(self):
        with test_directory() as folder:
            path = folder / "config.json"
            path.write_text('{"feishu":{"enable":false}}', encoding="utf-8")
            with self.assertRaises(push.NotificationError):
                send_feishu_test(path)
            self.post.assert_not_called()

    def test_setup_cancel_at_either_field_preserves_entire_file(self):
        for field in (0, 1):
            with self.subTest(field=field), test_directory() as folder, redirect_stdout(io.StringIO()):
                path = folder / "config.json"
                original = '{"username":"fake-user","feishu":{"enable":false}}'
                path.write_text(original, encoding="utf-8")
                entries = [MenuBack()] if field == 0 else [WEBHOOK, MenuBack()]
                result = run_notification_page(path, "setup", key_reader=lambda: "enter",
                                               field_reader=Mock(side_effect=entries))
                self.assertEqual(result, BACK_CODE)
                self.assertEqual(path.read_text(encoding="utf-8"), original)
        self.post.assert_not_called()

    def test_back_from_test_page_does_not_send(self):
        with test_directory() as folder, redirect_stdout(io.StringIO()):
            keys = iter(("down", "enter"))
            self.assertEqual(run_notification_page(folder / "missing.json", "test",
                                                   key_reader=lambda: next(keys)), BACK_CODE)
        self.post.assert_not_called()

    def test_test_page_sends_once_and_reports_only_api_acceptance(self):
        with test_directory() as folder, redirect_stdout(io.StringIO()) as output:
            path = folder / "config.json"
            path.write_text(json.dumps({"feishu": {"enable": True, "webhook": WEBHOOK}}), encoding="utf-8")
            self.assertEqual(run_notification_page(path, "test", key_reader=lambda: "enter"), BACK_CODE)
            self.assertIn("飞书接口已接受消息", output.getvalue())
            self.assertIn("请查看机器人所在群", output.getvalue())
        self.post.assert_called_once()

    def test_test_page_error_can_return_without_retrying(self):
        self.post.return_value.json.return_value = {"code": 19021}
        with test_directory() as folder, redirect_stdout(io.StringIO()) as output:
            path = folder / "config.json"
            path.write_text(json.dumps({"feishu": {"enable": True, "webhook": WEBHOOK}}), encoding="utf-8")
            keys = iter(("enter", "down", "enter"))
            self.assertEqual(run_notification_page(path, "test", key_reader=lambda: next(keys)), BACK_CODE)
            self.assertIn("操作未完成", output.getvalue())
            self.assertNotIn("飞书接口已接受消息", output.getvalue())
        self.post.assert_called_once()


if __name__ == "__main__":
    unittest.main()
