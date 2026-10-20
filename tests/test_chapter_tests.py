"""Offline protocol and save/recovery tests; no account or network access."""
from base64 import b64encode
import json
import io
from contextlib import redirect_stdout
from pathlib import Path
import shutil
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

import requests
from Crypto.Cipher import AES, PKCS1_v1_5
from Crypto.PublicKey import RSA
from Crypto.Util.Padding import unpad

from chapter_tests import (API_ROOT, IV, ChapterAPI, ChapterError, ChapterRunner,
                           PaperContext, Question, compact_json, parse_questions,
                           recover_key, run_chapter_cli, run_chapter_batch, run_chapter_selection,
                           UnsupportedChapter, validate_answers)
from ObjDict import ObjDict


def response(data):
    result = Mock()
    result.json.return_value = data
    return result


def raw_question(eid="q1", kind=1, name="Choose the correct answer."):
    return {"eid": eid, "questionType": {"id": kind}, "name": name,
            "questionOptions": [{"id": "option-91", "content": "<p>Yes</p>"},
                                {"id": "option-23", "content": "No"}]}


def paper(*questions):
    return {"state": 1, "examBase": {"chapterId": 7,
             "workExamParts": [{"questionDtos": list(questions or [raw_question()])}]}}


CONTEXT = PaperContext("recruit", "course", "student-exam", "exam", "school")


class CryptoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = RSA.generate(1024)
        cls.public = b64encode(cls.key.public_key().export_key(format="DER")).decode()

    def sign(self, payload, padding=b"\xff"):
        encoded = compact_json(payload).encode()
        block = b"\x00\x01" + padding * (self.key.size_in_bytes() - len(encoded) - 3) + b"\x00" + encoded
        return b64encode(pow(int.from_bytes(block, "big"), self.key.d, self.key.n)
                         .to_bytes(self.key.size_in_bytes(), "big")).decode()

    def test_recovers_dynamic_key_from_public_verifiable_envelope(self):
        self.assertEqual(recover_key(self.sign({"cKey": "0123456789abcdef"}), self.public), b"0123456789abcdef")

    def test_rejects_bad_padding_truncated_signature_and_bad_key(self):
        for value in [self.sign({"cKey": "0123456789abcdef"}, b"\x01"), "AA==",
                      self.sign({"cKey": "short"}), "invalid base64"]:
            with self.subTest(value=value[:8]), self.assertRaises(ChapterError):
                recover_key(value, self.public)

    def test_aes_matches_official_cryptojs_fixture_including_utf8(self):
        session = Mock()
        session.post.return_value = response({"status": 200, "rt": {}})
        api = ChapterAPI(session)
        api.key = b"0123456789abcdef"
        api.request("exam/getSingleRecruit", {"recruitId": "123", "name": "章节测试"})
        encrypted = session.post.call_args.kwargs["data"]["secretStr"]
        # Generated independently with the official base2 aesUtil.encrypt.
        self.assertEqual(encrypted, "V9KqJoI8RENCoEVvCHfNeJFJMHaIRKhUBujVPb1ZoxlGs3qXDV5DFkHJVh9hhX3w")


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.session = Mock()
        self.api = ChapterAPI(self.session)
        self.api.key = b"0123456789abcdef"

    def payload(self):
        from base64 import b64decode
        encrypted = self.session.post.call_args.kwargs["data"]["secretStr"]
        return json.loads(unpad(AES.new(self.api.key, AES.MODE_CBC, IV)
                                .decrypt(b64decode(encrypted)), 16))

    def test_save_uses_homework_envelope_and_option_ids(self):
        self.session.post.return_value = response({"status": 200, "rt": {"statu": 1}})
        question = parse_questions(paper(raw_question(kind=2)))[0]
        self.api.save_answer(CONTEXT, question, ["option-23", "option-91"])
        self.assertEqual(self.session.post.call_args.args[0], API_ROOT + "answer/saveStudentAnswer")
        payload = self.payload()
        self.assertEqual(payload["recruitId"], "recruit")
        row, = json.loads(payload["stuExamAnswer"])
        self.assertEqual(row["answer"], "option-23,option-91")
        self.assertEqual(row["eid"], "q1")
        self.assertEqual(row["stuExamId"], "student-exam")
        self.assertEqual(row["questionType"], 2)
        self.assertEqual(row["fromType"], 3)

    def test_http_success_is_not_answer_save_success(self):
        for data in [{"status": 200, "rt": {"statu": -10}},
                     {"status": 200, "rt": {"statu": -20}},
                     {"status": 200, "code": -12}, {"status": 401},
                     {"status": 200, "rt": {}}, {"status": 200, "code": -1}]:
            with self.subTest(data=data), self.assertRaises(ChapterError):
                self.session.post.return_value = response(data)
                self.api.save_answer(CONTEXT, parse_questions(paper())[0], ["option-91"])

    def test_open_uses_student_exam_id_not_save_field_name(self):
        self.session.post.return_value = response({"status": 200, "rt": paper()})
        self.api.open_paper(CONTEXT)
        self.assertEqual(self.payload()["studentExamId"], "student-exam")
        self.assertNotIn("stuExamId", self.payload())

    def test_fresh_paper_does_not_request_missing_answer_details(self):
        self.api.request = Mock(return_value={"rt": 0})
        self.assertEqual(self.api.read_answers(CONTEXT, ["q1"]), {})
        self.assertEqual(self.api.request.call_count, 1)

    def test_read_answers_uses_official_id_list(self):
        self.api.request = Mock(side_effect=[{"rt": 1}, {"rt": {"q1": {"answer": "option-91"}}}])
        self.assertEqual(self.api.read_answers(CONTEXT, ["q1"])["q1"]["answer"], "option-91")
        self.assertEqual(self.api.request.call_args.args[0], "answer/getStuAnswerInfoNew")
        self.assertEqual(self.api.request.call_args.args[1]["questionIds"], "q1")

    def test_list_paginates_and_rejects_duplicate_pages(self):
        self.api.request = Mock(side_effect=[
            {"rt": {"studentHomeworkList": [{"id": 1}], "next": 1}},
            {"rt": {"studentHomeworkList": [{"id": 2}], "next": -1}}])
        self.assertEqual([r["id"] for r in self.api.list_tests("r", "c")], [1, 2])
        self.assertEqual([c.args[1]["pageNum"] for c in self.api.request.call_args_list], [0, 1])
        self.api.request = Mock(return_value={"rt": {"studentHomeworkList": [{"id": 1}], "next": 1}})
        with self.assertRaisesRegex(ChapterError, "分页重复"):
            self.api.list_tests("r", "c")
        self.assertEqual(self.api.request.call_count, 2)

    def test_rejects_unrelated_write_endpoint(self):
        with self.assertRaises(ValueError):
            self.api.request("answer/applyRedo", {})
        self.session.post.assert_not_called()

    def test_submit_matches_official_homework_payload(self):
        self.session.post.return_value = response({"status": 200, "rt": {"statu": 0, "score": 80}})
        self.assertEqual(self.api.submit_paper(CONTEXT, 2)["score"], 80)
        self.assertEqual(self.session.post.call_args.args[0], API_ROOT + "answer/submit")
        self.assertEqual(self.payload(), {"recruitId": "recruit", "examId": "exam", "stuExamId": "student-exam", "achieveCount": 2})

    def test_submit_business_rejection_is_not_success(self):
        for status in (-1, -10, -20, None):
            self.session.post.return_value = response({"status": 200, "rt": {"statu": status}})
            with self.subTest(status=status), self.assertRaises(ChapterError):
                self.api.submit_paper(CONTEXT, 1)

    def test_detects_login_redirect(self):
        self.session.get.return_value.url = "https://passport.zhihuishu.com/login"
        with self.assertRaisesRegex(ChapterError, "重新扫码"):
            self.api.authenticate()

    def test_cas_callback_html_is_checked_by_api_identity(self):
        self.session.get.return_value.url = "https://studentexam-api.zhihuishu.com/studentExam/gateway/f/v1/gologin/login"
        self.session.cookies.get.return_value = '{"uuid":"same-user"}'
        self.api.request = Mock(return_value={"rt": {"uuid": "same-user"}})
        self.api.authenticate()
        self.api.request.assert_called_once_with("ulogin/getLoginUserInfo", {})

    def test_cas_identity_mismatch_is_not_accepted(self):
        self.session.get.return_value.url = "https://onlineexamh5new.zhihuishu.com/stuExamWeb.html"
        self.session.cookies.get.return_value = '{"uuid":"same-user"}'
        self.api.request = Mock(return_value={"rt": {"uuid": "other-user"}})
        with self.assertRaises(ChapterError):
            self.api.authenticate()


class QuestionTests(unittest.TestCase):
    def test_flattens_children_and_retains_parent_passage(self):
        parent = {"name": "<p>Read the passage.</p>", "questionChildrens": [raw_question()]}
        question, = parse_questions(paper(parent))
        self.assertIn("Read the passage.", question.text)
        self.assertIn("Choose", question.text)
        self.assertEqual(question.options[0]["content"], "Yes")

    def test_rejects_duplicate_question_ids(self):
        with self.assertRaisesRegex(ChapterError, "重复"):
            parse_questions(paper(raw_question(), raw_question()))

    def test_rejects_nonexistent_multiple_or_duplicate_choices(self):
        question = parse_questions(paper())[0]
        for answer in [["A"], ["option-91", "option-23"], ["option-91", "option-91"], [], None, [True]]:
            with self.subTest(answer=answer), self.assertRaises(ChapterError):
                validate_answers(question, answer)

    def test_media_and_unsupported_question_types_never_auto_save(self):
        for question in [raw_question(name='<img src="diagram.png">Choose.'),
                         raw_question(kind=3), {**raw_question(), "questionDatas": [{"id": 1}]}]:
            with self.assertRaises(ChapterError):
                validate_answers(parse_questions(paper(question))[0], ["option-91"])


class RunnerTests(unittest.TestCase):
    def setUp(self):
        # Python 3.14's Windows mkdir(0700) temporary directories are inaccessible
        # to the managed execution identity; use normal inherited workspace ACLs.
        root = Path(__file__).resolve().parent
        directory = root / ("chapter-test-" + uuid4().hex)
        directory.mkdir()

        def cleanup():
            if directory.resolve().parent != root or not directory.name.startswith("chapter-test-"):
                raise AssertionError("unexpected test cleanup path")
            shutil.rmtree(directory)

        self.addCleanup(cleanup)
        self.path = directory / "progress.json"
        self.api = Mock()
        self.solve = Mock(return_value=["option-91"])
        self.runner = ChapterRunner(self.api, CONTEXT, self.solve, self.path, output=Mock())

    def read_report(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def test_one_question_is_saved_and_verified_before_next(self):
        events = []
        reads = iter([{}, {"q1": {"answer": "option-91"}}, {"q2": {"answer": "option-91"}}])
        self.api.read_answers.side_effect = lambda *args: (events.append("read:" + args[1][0]), next(reads))[1]
        self.api.save_answer.side_effect = lambda ctx, q, answers: events.append("save:" + q.id)
        self.runner.run(paper(raw_question(), raw_question("q2")))
        self.assertEqual(events, ["read:q1", "save:q1", "read:q1", "save:q2", "read:q2"])
        report = self.read_report()
        self.assertEqual(report["status"], "saved")
        self.assertFalse(report["submitted"])
        self.api.submit.assert_not_called()

    def test_keeps_existing_answer_and_resumes_at_unanswered_question(self):
        self.api.read_answers.side_effect = [{"q1": {"answer": "option-23"}},
                                             {"q2": {"answer": "option-91"}}]
        self.runner.run(paper(raw_question(), raw_question("q2")))
        self.assertEqual(self.api.save_answer.call_count, 1)
        self.assertEqual(self.api.save_answer.call_args.args[1].id, "q2")
        self.assertEqual(self.read_report()["questions"]["q1"]["status"], "already_saved")

    def test_save_timeout_stops_without_replaying_or_processing_next_question(self):
        self.api.read_answers.return_value = {}
        self.api.save_answer.side_effect = requests.Timeout("timeout")
        with self.assertRaises(ChapterError):
            self.runner.run(paper(raw_question(), raw_question("q2")))
        self.assertEqual(self.api.save_answer.call_count, 1)
        self.assertEqual(self.solve.call_count, 1)
        report = self.read_report()
        self.assertEqual(report["questions"]["q1"]["previous_status"], "saving")
        self.assertEqual(report["status"], "stopped")

    def test_readback_mismatch_stops_and_records_accepted_state(self):
        self.api.read_answers.side_effect = [{}, {"q1": {"answer": "option-23"}}]
        with self.assertRaisesRegex(ChapterError, "回读答案不一致"):
            self.runner.run(paper(raw_question(), raw_question("q2")))
        self.assertEqual(self.read_report()["questions"]["q1"]["previous_status"], "accepted")
        self.assertEqual(self.solve.call_count, 1)

    def test_model_failure_does_not_send_random_answer(self):
        self.api.read_answers.return_value = {}
        self.solve.side_effect = ValueError("model unavailable")
        with self.assertRaises(ChapterError):
            self.runner.run(paper())
        self.api.save_answer.assert_not_called()

    def test_submitted_and_nonchapter_papers_are_not_modified(self):
        for data in [{**paper(), "state": 4}, {"examBase": {"workExamParts": []}}]:
            with self.assertRaises(ChapterError):
                self.runner.run(data)
        self.api.read_answers.assert_not_called()
        self.api.save_answer.assert_not_called()

    def test_submit_runs_once_after_final_read_and_confirms_server_state(self):
        events = []
        reads = iter([{}, {"q1": {"answer": "option-91"}}, {"q1": {"answer": "option-91"}}])
        self.api.read_answers.side_effect = lambda *args: (events.append("read"), next(reads))[1]
        self.api.save_answer.side_effect = lambda *args: events.append("save")
        self.api.submit_paper.side_effect = lambda *args: (events.append("submit"), {"statu": 0, "score": 90})[1]
        self.api.open_paper.side_effect = lambda *args: (events.append("confirm"), {"state": 4})[1]
        result = self.runner.run(paper(), submit=True)
        self.assertEqual(events, ["read", "save", "read", "read", "submit", "confirm"])
        self.assertTrue(result["submitted"])
        self.assertEqual(result["score"], 90)
        self.api.submit_paper.assert_called_once_with(CONTEXT, 1)

    def test_final_read_missing_or_changed_answer_blocks_submit(self):
        for final in [{}, {"q1": {"answer": "option-23"}}]:
            self.api.read_answers.side_effect = [{}, {"q1": {"answer": "option-91"}}, final]
            with self.assertRaisesRegex(ChapterError, "交卷前回读"):
                self.runner.run(paper(), submit=True)
            self.api.submit_paper.assert_not_called()
            self.assertFalse(self.read_report()["submitted"])

    def test_model_failure_in_submit_mode_never_submits(self):
        self.api.read_answers.return_value = {}
        self.solve.side_effect = ValueError("model failed")
        with self.assertRaises(ChapterError):
            self.runner.run(paper(), submit=True)
        self.api.submit_paper.assert_not_called()

    def test_submit_timeout_is_journaled_without_retry(self):
        self.api.read_answers.side_effect = [{}, {"q1": {"answer": "option-91"}}, {"q1": {"answer": "option-91"}}]
        self.api.submit_paper.side_effect = requests.Timeout("unknown result")
        with self.assertRaises(ChapterError):
            self.runner.run(paper(), submit=True)
        self.api.submit_paper.assert_called_once()
        self.api.open_paper.assert_not_called()
        self.assertIsNone(self.read_report()["submitted"])
        self.assertEqual(self.read_report()["status"], "submission_unconfirmed")

    def test_accepted_submit_without_submitted_state_is_not_success(self):
        self.api.read_answers.side_effect = [{}, {"q1": {"answer": "option-91"}}, {"q1": {"answer": "option-91"}}]
        self.api.submit_paper.return_value = {"statu": 1}
        self.api.open_paper.return_value = {"state": 1}
        with self.assertRaisesRegex(ChapterError, "未读到已提交状态"):
            self.runner.run(paper(), submit=True)
        self.assertEqual(self.read_report()["status"], "submission_unconfirmed")

    def test_question_count_mismatch_blocks_submit(self):
        self.api.read_answers.side_effect = [{}, {"q1": {"answer": "option-91"}}, {"q1": {"answer": "option-91"}}]
        data = paper()
        data["examBase"]["problemNum"] = 9
        with self.assertRaisesRegex(ChapterError, "题数"):
            self.runner.run(data, submit=True)
        self.api.submit_paper.assert_not_called()


class CliTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.client.uuid = "fake-account"
        self.client.getZhidaoList.return_value = [{"secret": "course-key", "courseName": "跨文化英语"}]
        self.client.getZhidaoContext.return_value = ObjDict({"course": {"recruitId": "recruit"}})
        self.api = Mock()
        self.api.request.side_effect = [{"rt": {"courseId": "course"}}, {"rt": {"schoolId": "school"}}]
        self.api.list_tests.return_value = [{"id": "student-exam", "examId": "exam", "examName": "第一章测试"}]
        self.api.open_paper.return_value = paper()

    def test_export_uses_selected_course_and_never_calls_ai_or_save(self):
        with patch("chapter_tests.ChapterAPI.from_client", return_value=self.api), \
             patch("chapter_tests.atomic_json") as write, patch("chapter_tests.AISolver") as solver, \
             patch("builtins.print"):
            run_chapter_cli(self.client, "export", ["course-key"], "student-exam")
        self.api.authenticate.assert_called_once()
        self.api.open_paper.assert_called_once_with(CONTEXT)
        self.assertEqual(write.call_args.args[1]["questions"][0]["id"], "q1")
        solver.assert_not_called()
        self.api.save_answer.assert_not_called()

    def test_list_never_opens_or_answers_a_paper(self):
        with patch("chapter_tests.ChapterAPI.from_client", return_value=self.api), \
             patch("chapter_tests.atomic_json") as write, patch("builtins.print"):
            run_chapter_cli(self.client, "list", ["course-key"])
        self.api.open_paper.assert_not_called()
        self.api.save_answer.assert_not_called()
        write.assert_not_called()

    def test_answer_dispatches_to_runner_without_starting_video_flow(self):
        with patch("chapter_tests.ChapterAPI.from_client", return_value=self.api), \
             patch("chapter_tests.atomic_json"), patch("chapter_tests.AISolver"), \
             patch("chapter_tests.ChapterRunner") as runner, patch("builtins.print"):
            run_chapter_cli(self.client, "answer", ["course-key"], "student-exam", {"enabled": True})
        runner.return_value.run.assert_called_once_with(self.api.open_paper.return_value, submit=False)
        self.client.fuckCourse.assert_not_called()

    def test_missing_or_foreign_test_id_does_not_open_any_paper(self):
        with patch("chapter_tests.ChapterAPI.from_client", return_value=self.api), \
             patch("builtins.print"), self.assertRaises(ChapterError):
            run_chapter_cli(self.client, "answer", ["course-key"], "foreign-exam")
        self.api.open_paper.assert_not_called()

    def test_list_export_and_back_keep_hierarchy_without_relogin(self):
        from terminal_ui import BACK_CODE
        self.api.request.side_effect = lambda endpoint, payload: {
            "rt": {"courseId": "course"} if endpoint == "exam/getSingleRecruit" else {"schoolId": "school"}}
        # Course -> list -> return -> export -> test -> return -> course -> main.
        keys = iter(["enter", "enter", "enter", "down", "enter", "enter", "enter", "back", "back", "back"])
        with patch("chapter_tests.sys.stdin.isatty", return_value=True), \
             patch("terminal_ui.read_menu_key", side_effect=lambda: next(keys)), \
             patch("chapter_tests.ChapterAPI.from_client", return_value=self.api), \
             patch("chapter_tests.atomic_json"), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(run_chapter_cli(self.client, "menu"), BACK_CODE)
        self.assertIn("未交测试列表", output.getvalue())
        self.assertIn("题目导出完成", output.getvalue())
        self.assertIn("返回上一页", output.getvalue())
        self.assertGreaterEqual(output.getvalue().count("选择一份章节测试"), 2)
        self.assertIn("返回课程列表", output.getvalue())
        self.assertIn("返回主菜单", output.getvalue())
        self.client.login.assert_not_called()
        self.api.save_answer.assert_not_called()

    def test_error_result_returns_to_operations_instead_of_exiting(self):
        from terminal_ui import BACK_CODE
        self.api.authenticate.side_effect = ChapterError("fake offline error")
        keys = iter(["enter", "enter", "enter", "back", "back"])
        with patch("chapter_tests.sys.stdin.isatty", return_value=True), \
             patch("terminal_ui.read_menu_key", side_effect=lambda: next(keys)), \
             patch("chapter_tests.ChapterAPI.from_client", return_value=self.api), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(run_chapter_cli(self.client, "menu"), BACK_CODE)
        self.assertIn("操作未完成", output.getvalue())
        self.assertIn("fake offline error", output.getvalue())


class BatchAndSelectionTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.client.uuid = "fake-account"
        self.course = {"secret": "fake-course", "courseName": "跨文化英语"}
        self.item = {"id": "paper-1", "examId": "exam-1", "examName": "第一章测试", "chapterId": 7}

    def test_batch_skips_unrelated_items_and_defaults_to_save_only(self):
        items = [{"id": "other", "examName": "test"}, self.item,
                 {**self.item, "id": "meeting", "meetCourseType": 1}]
        with patch("chapter_tests.atomic_json"), patch("chapter_tests.run_chapter_cli", return_value={"status": "saved", "progress": "fake.json"}) as run:
            result = run_chapter_batch(self.client, self.course, items, {}, output=Mock())
        self.assertEqual([i["status"] for i in result["items"]], ["skipped", "saved", "skipped"])
        self.assertFalse(run.call_args.kwargs["submit"])
        self.assertEqual(result["status"], "batch_completed")

    def test_batch_submit_choice_is_forwarded_to_each_test(self):
        with patch("chapter_tests.atomic_json"), patch("chapter_tests.run_chapter_cli", return_value={"status": "submitted", "progress": "fake.json"}) as run:
            result = run_chapter_batch(self.client, self.course, [self.item, {**self.item, "id": "paper-2"}], {}, submit=True, output=Mock())
        self.assertEqual(run.call_count, 2)
        self.assertTrue(all(c.kwargs["submit"] for c in run.call_args_list))
        self.assertTrue(all(i["status"] == "submitted" for i in result["items"]))

    def test_batch_continues_after_unsupported_question_type(self):
        with patch("chapter_tests.atomic_json"), patch("chapter_tests.run_chapter_cli", side_effect=[UnsupportedChapter("needs audio"), {"status": "saved", "progress": "fake.json"}]):
            result = run_chapter_batch(self.client, self.course, [self.item, {**self.item, "id": "paper-2"}], {}, output=Mock())
        self.assertEqual([i["status"] for i in result["items"]], ["skipped", "saved"])

    def test_batch_stops_after_uncertain_submission_and_does_not_start_next(self):
        with patch("chapter_tests.atomic_json"), patch("chapter_tests.run_chapter_cli", side_effect=ChapterError("submission unknown")) as run:
            result = run_chapter_batch(self.client, self.course, [self.item, {**self.item, "id": "paper-2"}], {}, submit=True, output=Mock())
        run.assert_called_once()
        self.assertEqual(result["status"], "batch_stopped")
        self.assertEqual(result["remaining"], 1)

    def test_all_button_and_save_or_submit_choice_are_available(self):
        for submit in (False, True):
            listing = {"status": "listed", "tests": [self.item]}
            result = {"status": "batch_completed", "course": "跨文化英语", "items": [], "remaining": 0,
                      "submit": submit, "progress": "fake-batch.json"}
            keys = iter(["down", "enter"] + (["down", "enter"] if submit else ["enter"]) + ["back"])
            with self.subTest(submit=submit), patch("chapter_tests.sys.stdin.isatty", return_value=True), \
                 patch("terminal_ui.read_menu_key", side_effect=lambda: next(keys)), \
                 patch("chapter_tests.run_chapter_cli", side_effect=[listing, result, listing]) as run, \
                 patch("terminal_ui.show_result_page"), redirect_stdout(io.StringIO()) as output:
                run_chapter_selection(self.client, self.course, "answer", {})
            self.assertIn("一键全做", output.getvalue())
            self.assertIn("仅保存", output.getvalue())
            self.assertIn("保存并提交", output.getvalue())
            execution = run.call_args_list[1]
            self.assertTrue(execution.kwargs["all_tests"])
            self.assertEqual(execution.kwargs["submit"], submit)

    def test_back_from_mode_returns_to_test_list_without_starting_work(self):
        listing = {"status": "listed", "tests": [self.item]}
        keys = iter(["enter", "back", "back"])
        with patch("chapter_tests.sys.stdin.isatty", return_value=True), \
             patch("terminal_ui.read_menu_key", side_effect=lambda: next(keys)), \
             patch("chapter_tests.run_chapter_cli", return_value=listing) as run, redirect_stdout(io.StringIO()):
            run_chapter_selection(self.client, self.course, "answer", {})
        self.assertEqual(run.call_count, 2)
        self.assertTrue(all(c.args[1] == "list" for c in run.call_args_list))


if __name__ == "__main__":
    unittest.main()
