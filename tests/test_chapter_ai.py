"""AI settings and completion-protocol checks without real credentials or HTTP."""
import json
import io
from contextlib import redirect_stdout
from pathlib import Path
import shutil
import sys
import types
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from chapter_ai_settings import save_settings, run_ai_settings_page
from terminal_ui import BACK_CODE

fake_logger = types.ModuleType("logger")
fake_logger.logger = Mock()
with patch.dict(sys.modules, {"logger": fake_logger}):
    from fucker import Openai


class SettingsTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parent
        directory = root / ("chapter-ai-test-" + uuid4().hex)
        directory.mkdir()

        def cleanup():
            if directory.resolve().parent != root or not directory.name.startswith("chapter-ai-test-"):
                raise AssertionError("unexpected cleanup target")
            shutil.rmtree(directory)

        self.addCleanup(cleanup)
        self.path = directory / "config.json"

    def test_preserves_accounts_and_notifications_when_configuring_deepseek(self):
        original = {"username": "fake-user", "password": "fake-password", "feishu": {"enable": True},
                    "ai": {"ppt_processing": {"provide_to_ai": False}, "openai": {"custom": "keep"}}}
        self.path.write_text(json.dumps(original), encoding="utf-8")
        save_settings(self.path, "https://api.deepseek.com/", "deepseek-v4-flash", "fake-api-key")
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(saved["username"], original["username"])
        self.assertEqual(saved["password"], original["password"])
        self.assertEqual(saved["feishu"], original["feishu"])
        self.assertEqual(saved["ai"]["ppt_processing"], original["ai"]["ppt_processing"])
        self.assertEqual(saved["ai"]["openai"]["custom"], "keep")
        self.assertEqual(saved["ai"]["openai"]["model_name"], "deepseek-v4-flash")
        self.assertFalse(saved["ai"]["use_zhidao_ai"])
        self.assertFalse(saved["ai"]["use_stream"])

    def test_invalid_settings_do_not_overwrite_existing_file(self):
        self.path.write_text('{"keep":true}', encoding="utf-8")
        for url, model, key in [("http://example.com", "model", "key"),
                                ("https://user:key@example.com", "model", "key"),
                                ("https://example.com?key=secret", "model", "key"),
                                ("https://api.deepseek.com", "", "key"),
                                ("https://api.deepseek.com", "model", "")]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                save_settings(self.path, url, model, key)
            self.assertEqual(self.path.read_text(encoding="utf-8"), '{"keep":true}')

    def test_cancel_at_every_field_preserves_configuration(self):
        original = {"ai": {"openai": {"api_base": "https://api.deepseek.com", "model_name": "deepseek-v4-flash", "api_key": "fake-keep-key"}}}
        self.path.write_text(json.dumps(original), encoding="utf-8")
        for fields in [["/back"], ["", "/back"], ["", "", "/back"]]:
            with self.subTest(fields=fields), redirect_stdout(io.StringIO()) as output:
                keys, inputs = iter(["enter"]), iter(fields)
                self.assertEqual(run_ai_settings_page(self.path, key_reader=lambda: next(keys),
                                                     field_reader=lambda label: next(inputs)), BACK_CODE)
                self.assertNotIn("fake-keep-key", output.getvalue())
            self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), original)

    def test_saved_result_returns_to_settings_menu_without_revealing_key(self):
        keys, fields = iter(["enter", "enter", "back"]), iter(["https://api.deepseek.com", "deepseek-v4-flash", "fake-new-key"])
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(run_ai_settings_page(self.path, key_reader=lambda: next(keys),
                                                 field_reader=lambda label: next(fields)), BACK_CODE)
        self.assertIn("配置已保存", output.getvalue())
        self.assertIn("返回上一级", output.getvalue())
        self.assertNotIn("fake-new-key", output.getvalue())

    def test_changing_provider_does_not_reuse_key_from_previous_host(self):
        original = {"ai": {"openai": {"api_base": "https://old.invalid", "model_name": "old", "api_key": "fake-old-key"}}}
        self.path.write_text(json.dumps(original), encoding="utf-8")
        keys, fields = iter(["enter", "enter", "back"]), iter(["https://new.invalid", "new-model", ""])
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(run_ai_settings_page(self.path, key_reader=lambda: next(keys),
                                                 field_reader=lambda label: next(fields)), BACK_CODE)
        self.assertIn("配置未保存", output.getvalue())
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), original)


class CompletionTests(unittest.TestCase):
    def setUp(self):
        self.model = Openai.__new__(Openai)
        self.model.apiKey = "fake-api-key"
        self.model.modelName = "deepseek-v4-flash"
        self.model.stream = False

    def test_compatible_base_urls_do_not_duplicate_v1(self):
        reply = Mock()
        reply.json.return_value = {"choices": [{"message": {"content": "answer"}}]}
        for base, expected in [("https://api.deepseek.com", "https://api.deepseek.com/v1/chat/completions"),
                               ("https://api.deepseek.com/v1/", "https://api.deepseek.com/v1/chat/completions"),
                               ("https://example.com/api/v4", "https://example.com/api/v4/chat/completions"),
                               ("https://example.com/chat/completions", "https://example.com/chat/completions")]:
            with self.subTest(base=base), patch("fucker.requests.post", return_value=reply) as post:
                self.model.baseUrl = base
                self.assertEqual(self.model.openaiCompletion("question"), "answer")
                self.assertEqual(post.call_args.args[0], expected)
                self.assertEqual(post.call_args.kwargs["json"]["model"], "deepseek-v4-flash")

    def test_json_business_error_in_stream_is_not_silently_dropped(self):
        reply = Mock()
        reply.iter_lines.return_value = [b'{"code":"509","message":"service unavailable","data":null}']
        with self.assertRaisesRegex(ValueError, "509"):
            self.model._Openai__parseStream(reply, "```answer", "```")
        reply.close.assert_called_once()

    def test_streaming_chunks_are_joined_and_response_closed(self):
        reply = Mock()
        reply.iter_lines.return_value = [b': keepalive',
            b'data: {"choices":[{"delta":{"content":"```answer\\n"}}]}',
            b'data: {"choices":[{"delta":{"content":"[]\\n```"}}]}', b'data: [DONE]']
        self.assertEqual(self.model._Openai__parseStream(reply, "```answer", "```"), "```answer\n[]\n```")
        reply.close.assert_called_once()

    def test_empty_stream_raises_explicit_error(self):
        reply = Mock()
        reply.iter_lines.return_value = [b'data: [DONE]']
        with self.assertRaisesRegex(ValueError, "未返回文本"):
            self.model._Openai__parseStream(reply, "```answer", "```")

    def test_null_completion_never_reaches_regex_parser(self):
        self.model.encoder = Mock()
        self.model.encoder.encode.return_value = [1]
        self.model.useZhidao = False
        self.model.openaiCompletion = Mock(return_value=None)
        with self.assertRaisesRegex(ValueError, "有效文本"):
            self.model.generateAnswer("question")


if __name__ == "__main__":
    unittest.main()
