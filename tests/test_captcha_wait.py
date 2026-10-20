"""Offline regression checks; no account, network, or project log access."""
import json
import sys
import types
import unittest
from unittest.mock import Mock, call, patch

# The real logger truncates existing logs at import time. Preserve those logs.
test_logger = types.ModuleType("logger")
test_logger.logger = Mock()
with patch.dict(sys.modules, {"logger": test_logger}):
    import fucker

from ObjDict import ObjDict
from zd_utils import Cipher


class CaptchaWaitTests(unittest.TestCase):
    def setUp(self):
        self.client = fucker.Fucker.__new__(fucker.Fucker)
        self.client._apiQuery = Mock()
        self.client._pushplus = Mock()
        self.client._bark = Mock()
        self.client._feishu = Mock()
        self.url = "https://example.invalid/test"
        self.logger = Mock()
        logger_patch = patch.object(fucker, "logger", self.logger)
        logger_patch.start()
        self.addCleanup(logger_patch.stop)
        for target in ("fucker.time.sleep", "builtins.print"):
            patcher = patch(target)
            mocked = patcher.start()
            self.addCleanup(patcher.stop)
            if target == "fucker.time.sleep":
                self.sleep = mocked

    def test_waits_repeatedly_and_retries_same_progress_with_fresh_timestamp(self):
        success = ObjDict({"code": 0, "data": {"accepted": True}})
        self.client._apiQuery.side_effect = [
            ObjDict({"code": -12}), ObjDict({"code": -12}), success]
        with patch("fucker.time.time", side_effect=[100, 115, 130]):
            result = self.client.zhidaoQuery(self.url, {"played_time": 42})
        self.assertIs(result, success)
        self.assertEqual(self.sleep.call_args_list, [call(15), call(15)])
        self.client._pushplus.assert_called_once()
        self.client._bark.assert_called_once()
        self.client._feishu.assert_called_once()
        forms = [c.kwargs["data"] for c in self.client._apiQuery.call_args_list]
        payloads = [json.loads(Cipher().decrypt(f["secretStr"])) for f in forms]
        self.assertEqual([p["dateFormate"] for p in payloads], [100000, 115000, 130000])
        self.assertEqual([p["played_time"] for p in payloads], [42, 42, 42])

    def test_captcha_is_handled_even_when_other_code_checks_are_disabled(self):
        success = ObjDict({"code": 200})
        self.client._apiQuery.side_effect = [ObjDict({"code": -12}), success]
        result = self.client.zhidaoQuery(
            self.url, {}, ok_code=None, encrypt=False, setTimeStamp=False,
            method="GET")
        self.assertIs(result, success)
        self.sleep.assert_called_once_with(15)
        for request in self.client._apiQuery.call_args_list:
            self.assertEqual(request.kwargs, {"data": {}, "method": "GET", "contentType": "form"})

    def test_notification_failure_does_not_end_wait(self):
        self.client._pushplus.side_effect = RuntimeError("notification unavailable")
        self.client._apiQuery.side_effect = [ObjDict({"code": -12}), ObjDict({"code": 0})]
        self.client.zhidaoQuery(self.url, {})
        self.client._bark.assert_called_once()
        self.client._feishu.assert_called_once()
        self.sleep.assert_called_once_with(15)

    def test_transient_network_failure_during_wait_is_retried(self):
        self.client._apiQuery.side_effect = [
            ObjDict({"code": -12}),
            fucker.requests.exceptions.Timeout("offline"),
            ObjDict({"code": 0})]
        self.client.zhidaoQuery(self.url, {})
        self.assertEqual(self.client._apiQuery.call_count, 3)
        self.assertEqual(self.sleep.call_count, 2)
        self.client._pushplus.assert_called_once()

    def test_network_failure_before_captcha_still_propagates(self):
        self.client._apiQuery.side_effect = fucker.requests.exceptions.Timeout("offline")
        with self.assertRaises(fucker.requests.exceptions.Timeout):
            self.client.zhidaoQuery(self.url, {})
        self.sleep.assert_not_called()

    def test_other_api_errors_do_not_claim_verification_success(self):
        self.client._apiQuery.side_effect = [
            ObjDict({"code": -12}), ObjDict({"code": -1, "message": "expired"})]
        with self.assertRaisesRegex(Exception, "expired"):
            self.client.zhidaoQuery(self.url, {})
        self.assertEqual(self.client._apiQuery.call_count, 2)
        messages = [str(c) for c in self.logger.info.call_args_list]
        self.assertFalse(any("请求已成功" in message for message in messages))

    def test_ctrl_c_interrupts_wait_without_another_request(self):
        self.client._apiQuery.return_value = ObjDict({"code": -12})
        self.sleep.side_effect = KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            self.client.zhidaoQuery(self.url, {})
        self.client._apiQuery.assert_called_once()

    def test_new_verification_block_sends_one_new_feishu_notification(self):
        self.client._apiQuery.side_effect = [
            ObjDict({"code": -12}), ObjDict({"code": 0}),
            ObjDict({"code": -12}), ObjDict({"code": 0})]
        self.client.zhidaoQuery(self.url, {})
        self.client.zhidaoQuery(self.url, {})
        self.assertEqual(self.client._feishu.call_count, 2)

    def test_feishu_failure_does_not_stop_wait_or_log_webhook(self):
        self.client._feishu.side_effect = RuntimeError("fake-private-webhook")
        self.client._apiQuery.side_effect = [ObjDict({"code": -12}), ObjDict({"code": 0})]
        result = self.client.zhidaoQuery(self.url, {"password": "fake-private-password"})
        self.assertEqual(result.code, 0)
        self.client._feishu.assert_called_once()
        self.assertNotIn("fake-private-password", str(self.client._feishu.call_args))
        self.assertNotIn("fake-private-webhook", str(self.logger.warning.call_args_list))

    def test_successful_requests_do_not_send_verification_notifications(self):
        self.client._apiQuery.return_value = ObjDict({"code": 0})
        self.client.zhidaoQuery(self.url, {})
        self.client._feishu.assert_not_called()
        self.client._pushplus.assert_not_called()
        self.client._bark.assert_not_called()


if __name__ == "__main__":
    unittest.main()
