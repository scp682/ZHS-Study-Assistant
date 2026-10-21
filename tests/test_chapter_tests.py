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
                           UnsupportedChapter, validate_answers, homework_kind)
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


def meeting_paper(*questions):
    data = paper(*questions)
    data["examBase"].pop("chapterId")
    return data


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

    def test_empty_pending_list_can_be_omitted_on_terminal_page(self):
        # Observed on the real API after all pending homework was submitted.
        for result in ({"next": -1}, {"next": "-1", "studentHomeworkList": None},
                       {"next": -1, "studentHomeworkList": []}):
            with self.subTest(result=result):
                self.session.post.return_value = response({"status": 200, "rt": result})
                self.assertEqual(self.api.list_tests("recruit", "course"), [])

    def test_omitted_final_page_preserves_items_from_previous_pages(self):
        self.api.request = Mock(side_effect=[
            {"rt": {"studentHomeworkList": [{"id": "pending"}], "next": 1}},
            {"rt": {"next": -1}}])
        self.assertEqual(self.api.list_tests("recruit", "course"), [{"id": "pending"}])
        self.assertEqual(self.api.request.call_count, 2)

    def test_missing_list_without_terminal_marker_is_still_an_error(self):
        for result in ({}, {"next": 1}, {"next": 0, "studentHomeworkList": None},
                       {"next": -1, "studentHomeworkList": {}}, None):
            with self.subTest(result=result), self.assertRaises(ChapterError):
                self.session.post.return_value = response({"status": 200, "rt": result})
                self.api.list_tests("recruit", "course")

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
        self.session.cookies.get.return_value = '{"uuid":"same-user"}'
        self.api.request = Mock(return_value={"rt": {"loginstatus": False}})
        with self.assertRaisesRegex(ChapterError, "返回登录首页"):
            self.api.authenticate()

    def test_cas_callback_html_is_checked_by_api_identity(self):
        self.session.get.return_value.url = "https://studentexam-api.zhihuishu.com/studentExam/gateway/f/v1/gologin/login"
        self.session.cookies.get.return_value = '{"uuid":"same-user"}'
        self.api.request = Mock(side_effect=[{"rt": {"loginstatus": False}}, {"rt": {"uuid": "same-user"}}])
        self.api.authenticate()
        self.assertEqual(self.api.request.call_count, 2)
        self.session.get.assert_called_once()

    def test_cas_identity_mismatch_is_not_accepted(self):
        self.session.get.return_value.url = "https://onlineexamh5new.zhihuishu.com/stuExamWeb.html"
        self.session.cookies.get.return_value = '{"uuid":"same-user"}'
        self.api.request = Mock(return_value={"rt": {"uuid": "other-user"}})
        with self.assertRaises(ChapterError):
            self.api.authenticate()


class SharedLoginTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.client.cookies = requests.cookies.RequestsCookieJar()
        self.client.cookies.set("CASLOGC", '{"uuid":"same-user"}', domain=".zhihuishu.com")
        self.client.session = requests.Session()
        self.client.proxies = {}
        self.client.headers = {"User-Agent": "test-agent"}
        self.api = ChapterAPI.from_client(self.client)
        self.api.session.get = Mock()
        self.api.request = Mock(return_value={"rt": {"uuid": "same-user", "loginstatus": True}})

    def test_valid_exam_session_does_not_require_a_new_cas_ticket(self):
        self.api.session.cookies.set("JSESSIONID", "exam-session", domain="studentexam-api.zhihuishu.com")
        self.api.authenticate()
        self.api.session.get.assert_not_called()
        self.assertEqual(self.client.cookies.get("JSESSIONID"), "exam-session")

    def test_video_session_reset_does_not_destroy_exam_login(self):
        self.api.session.cookies.set("JSESSIONID", "exam-session", domain="studentexam-api.zhihuishu.com")
        self.api.authenticate()
        self.client.session.cookies.clear()
        self.client.session.cookies.set("JSESSIONID", "video-session", domain="studyservice-api.zhihuishu.com")
        api = ChapterAPI.from_client(self.client)
        self.assertIs(api, self.api)
        api.authenticate()
        self.assertEqual(api.session.cookies.get("JSESSIONID"), "exam-session")
        self.api.session.get.assert_not_called()

    def test_changing_account_does_not_reuse_exam_session(self):
        self.client.cookies.set("CASLOGC", '{"uuid":"other-user"}', domain=".zhihuishu.com")
        self.assertIsNot(ChapterAPI.from_client(self.client), self.api)

    def test_cas_redirect_cannot_replace_the_selected_identity(self):
        self.api.request.side_effect = [{"rt": {"loginstatus": False}}, {"rt": {"uuid": "other-user"}}]
        def redirect(*args, **kwargs):
            self.api.session.cookies.set("CASLOGC", '{"uuid":"other-user"}', domain=".zhihuishu.com")
            return Mock(url="https://onlineexamh5new.zhihuishu.com/stuExamWeb.html")
        self.api.session.get.side_effect = redirect
        with self.assertRaises(ChapterError):
            self.api.authenticate()
        self.assertIn('same-user', self.client.cookies.get("CASLOGC"))

    def test_invalid_loginstatus_is_not_accepted_even_with_uuid(self):
        self.api.request.return_value = {"rt": {"uuid": "same-user", "loginstatus": False}}
        with self.assertRaises(ChapterError):
            self.api.authenticate()

    def test_failed_exam_login_does_not_contaminate_video_cookies(self):
        self.api.request.return_value = {"rt": {"loginstatus": False}}
        self.api.session.cookies.set("JSESSIONID", "unverified", domain="studentexam-api.zhihuishu.com")
        self.api.session.get.return_value = Mock(url="https://passport.zhihuishu.com/login")
        with self.assertRaises(ChapterError):
            self.api.authenticate()
        self.assertIsNone(self.client.cookies.get("JSESSIONID"))


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

    def test_meeting_without_chapter_id_is_saved_and_read_back(self):
        self.api.read_answers.side_effect = [{}, {"q1": {"answer": "option-91"}}]
        result = self.runner.run(meeting_paper(), meeting=True)
        self.assertEqual(result["status"], "saved")
        self.assertEqual(self.read_report()["kind"], "meeting")
        self.api.save_answer.assert_called_once()
        self.api.submit_paper.assert_not_called()

    def test_other_homework_without_chapter_id_is_saved_and_read_back(self):
        self.api.read_answers.side_effect = [{}, {"q1": {"answer": "option-91"}}]
        result = self.runner.run(meeting_paper(), homework=True)
        self.assertEqual((result["status"], result["kind"]), ("saved", "other"))
        self.api.save_answer.assert_called_once()
        self.assertEqual(self.api.read_answers.call_count, 2)
        self.api.submit_paper.assert_not_called()

    def test_other_homework_submit_requires_final_read_and_confirmed_state(self):
        self.api.read_answers.side_effect = [{}, {"q1": {"answer": "option-91"}}, {"q1": {"answer": "option-91"}}]
        self.api.submit_paper.return_value = {"statu": 1}
        self.api.open_paper.return_value = {"state": 4}
        result = self.runner.run(meeting_paper(), homework=True, submit=True)
        self.assertEqual((result["status"], result["kind"]), ("submitted", "other"))
        self.assertTrue(result["submitted"])
        self.api.submit_paper.assert_called_once_with(CONTEXT, 1)

    def test_submitted_other_homework_is_not_reopened_for_answering(self):
        with self.assertRaisesRegex(ChapterError, "已提交或已结束"):
            self.runner.run({**meeting_paper(), "state": 4}, homework=True, submit=True)
        self.solve.assert_not_called()
        self.api.save_answer.assert_not_called()
        self.api.submit_paper.assert_not_called()

    def test_other_homework_readback_mismatch_never_submits(self):
        self.api.read_answers.side_effect = [{}, {"q1": {"answer": "option-23"}}]
        with self.assertRaisesRegex(ChapterError, "回读答案不一致"):
            self.runner.run(meeting_paper(), homework=True, submit=True)
        self.api.submit_paper.assert_not_called()

    def test_meeting_submit_still_requires_final_read_and_server_confirmation(self):
        self.api.read_answers.side_effect = [{}, {"q1": {"answer": "option-91"}}, {"q1": {"answer": "option-91"}}]
        self.api.submit_paper.return_value = {"statu": 1}
        self.api.open_paper.return_value = {"state": 4}
        result = self.runner.run(meeting_paper(), submit=True, meeting=True)
        self.assertTrue(result["submitted"])
        self.assertEqual(result["kind"], "meeting")
        self.api.submit_paper.assert_called_once_with(CONTEXT, 1)

    def test_meeting_readback_mismatch_never_submits(self):
        self.api.read_answers.side_effect = [{}, {"q1": {"answer": "option-23"}}]
        with self.assertRaisesRegex(ChapterError, "回读答案不一致"):
            self.runner.run(meeting_paper(), submit=True, meeting=True)
        self.api.submit_paper.assert_not_called()

    def test_submitted_meeting_is_not_answered_or_resubmitted(self):
        with self.assertRaisesRegex(ChapterError, "已提交或已结束"):
            self.runner.run({**meeting_paper(), "state": 4}, submit=True, meeting=True)
        self.api.read_answers.assert_not_called()
        self.solve.assert_not_called()
        self.api.save_answer.assert_not_called()
        self.api.submit_paper.assert_not_called()

    def test_meeting_media_question_is_not_sent_to_model_or_saved(self):
        self.api.read_answers.return_value = {}
        with self.assertRaises(ChapterError):
            self.runner.run(meeting_paper(raw_question(name='<img src="tea.png">Choose.')), meeting=True)
        self.solve.assert_not_called()
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

    def test_empty_pending_list_never_opens_paper_or_starts_ai(self):
        self.api.list_tests.return_value = []
        for action in ("list", "export", "answer"):
            with self.subTest(action=action), patch("chapter_tests.ChapterAPI.from_client", return_value=self.api), \
                 patch("chapter_tests.AISolver") as solver, patch("chapter_tests.atomic_json") as write:
                self.api.request.return_value = {"rt": {"courseId": "course"}}
                self.api.request.side_effect = None
                result = run_chapter_cli(self.client, action, ["course-key"], output=Mock())
                from chapter_tests import chapter_result
                self.assertEqual(chapter_result(result)[0], "没有未交测试")
                solver.assert_not_called()
                write.assert_not_called()
        self.api.open_paper.assert_not_called()
        self.api.save_answer.assert_not_called()
        self.api.submit_paper.assert_not_called()

    def test_empty_list_menu_shows_no_pending_work_instead_of_failure(self):
        from terminal_ui import BACK_CODE
        self.api.list_tests.return_value = []
        keys = iter(["enter", "enter", "enter", "back", "back"])
        with patch("chapter_tests.sys.stdin.isatty", return_value=True), \
             patch("chapter_tests.ChapterAPI.from_client", return_value=self.api), \
             patch("terminal_ui.read_menu_key", side_effect=lambda: next(keys)), \
             redirect_stdout(io.StringIO()) as output:
            self.assertEqual(run_chapter_cli(self.client, "menu"), BACK_CODE)
        self.assertIn("当前课程没有未交测试/作业。", output.getvalue())
        self.assertNotIn("操作未完成", output.getvalue())
        self.api.open_paper.assert_not_called()

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
        runner.return_value.run.assert_called_once_with(self.api.open_paper.return_value, submit=False, meeting=False, homework=False)
        self.client.fuckCourse.assert_not_called()

    def test_meeting_from_list_runs_full_save_and_verification_flow(self):
        self.api.list_tests.return_value[0]["meetCourseType"] = "1"
        self.api.open_paper.return_value = meeting_paper()
        self.api.read_answers.side_effect = [{}, {"q1": {"answer": "option-91"}}]
        with patch("chapter_tests.ChapterAPI.from_client", return_value=self.api), \
             patch("chapter_tests.atomic_json") as write, \
             patch("chapter_tests.AISolver", return_value=lambda q: ["option-91"]), \
             patch("builtins.print"):
            result = run_chapter_cli(self.client, "answer", ["course-key"], "student-exam", {})
        self.assertEqual(result["status"], "saved")
        self.assertEqual(result["kind"], "meeting")
        self.api.save_answer.assert_called_once()
        self.assertEqual(self.api.save_answer.call_args.args[0], CONTEXT)
        self.assertEqual(self.api.read_answers.call_count, 2)
        self.api.submit_paper.assert_not_called()
        self.assertEqual(write.call_args_list[0].args[1]["kind"], "meeting")

    def test_meeting_can_be_exported_without_calling_ai_or_save(self):
        self.api.list_tests.return_value[0]["meetCourseType"] = 1
        self.api.open_paper.return_value = meeting_paper()
        with patch("chapter_tests.ChapterAPI.from_client", return_value=self.api), \
             patch("chapter_tests.atomic_json"), patch("chapter_tests.AISolver") as solver, \
             patch("builtins.print"):
            result = run_chapter_cli(self.client, "export", ["course-key"], "student-exam")
        self.assertEqual(result["kind"], "meeting")
        self.assertEqual(result["count"], 1)
        solver.assert_not_called()
        self.api.save_answer.assert_not_called()

    def test_mixed_batch_runs_cli_and_runner_for_all_ordinary_homework(self):
        self.api.list_tests.return_value = [
            {"id": "chapter", "examId": "exam-1", "examName": "第一章", "chapterId": 7},
            {"id": "meeting", "examId": "exam-2", "examName": "茶与健康", "meetCourseType": 1},
            {"id": "other", "examId": "exam-3", "examName": "test"},
            {"id": "peer", "examName": "互评", "reviewType": 1}]
        self.api.request.side_effect = lambda endpoint, payload: {
            "rt": {"courseId": "course"} if endpoint == "exam/getSingleRecruit" else {"schoolId": "school"}}
        self.api.open_paper.side_effect = lambda context: paper() if context.student_exam_id == "chapter" else meeting_paper()
        self.api.read_answers.side_effect = [{}, {"q1": {"answer": "option-91"}}, {}, {"q1": {"answer": "option-91"}}, {}, {"q1": {"answer": "option-91"}}]
        with patch("chapter_tests.ChapterAPI.from_client", return_value=self.api), \
             patch("chapter_tests.atomic_json"), \
             patch("chapter_tests.AISolver", return_value=lambda q: ["option-91"]), \
             patch("builtins.print"):
            result = run_chapter_cli(self.client, "answer", ["course-key"], ai_config={}, all_tests=True)
        self.assertEqual([i["status"] for i in result["items"]], ["saved", "saved", "saved", "skipped"])
        self.assertEqual([c.args[0].student_exam_id for c in self.api.save_answer.call_args_list], ["chapter", "meeting", "other"])
        self.assertEqual(self.api.read_answers.call_count, 6)
        self.api.submit_paper.assert_not_called()

    def test_peer_review_is_rejected_even_when_marked_as_meeting(self):
        self.api.list_tests.return_value[0].update(meetCourseType=1, reviewType=1)
        with patch("chapter_tests.ChapterAPI.from_client", return_value=self.api), \
             patch("builtins.print"), self.assertRaisesRegex(UnsupportedChapter, "互评"):
            run_chapter_cli(self.client, "answer", ["course-key"], "student-exam")
        self.api.open_paper.assert_not_called()
        self.api.save_answer.assert_not_called()

    def test_ordinary_homework_without_chapter_or_meeting_marker_is_answered(self):
        self.api.list_tests.return_value[0]["examName"] = "test"
        self.api.open_paper.return_value = meeting_paper()
        self.api.read_answers.side_effect = [{}, {"q1": {"answer": "option-91"}}]
        with patch("chapter_tests.ChapterAPI.from_client", return_value=self.api), \
             patch("chapter_tests.atomic_json"), patch("chapter_tests.AISolver", return_value=lambda q: ["option-91"]), \
             patch("builtins.print"):
            result = run_chapter_cli(self.client, "answer", ["course-key"], "student-exam")
        self.assertEqual((result["status"], result["kind"]), ("saved", "other"))
        self.api.save_answer.assert_called_once()
        self.assertEqual(self.api.read_answers.call_count, 2)
        self.api.submit_paper.assert_not_called()

    def test_ordinary_homework_can_be_submitted_and_confirmed(self):
        self.api.open_paper.side_effect = [meeting_paper(), {"state": 4}]
        self.api.read_answers.side_effect = [{}, {"q1": {"answer": "option-91"}}, {"q1": {"answer": "option-91"}}]
        self.api.submit_paper.return_value = {"statu": 1}
        with patch("chapter_tests.ChapterAPI.from_client", return_value=self.api), \
             patch("chapter_tests.atomic_json"), patch("chapter_tests.AISolver", return_value=lambda q: ["option-91"]):
            result = run_chapter_cli(self.client, "answer", ["course-key"], "student-exam", submit=True, output=Mock())
        self.assertEqual((result["status"], result["kind"]), ("submitted", "other"))
        self.api.submit_paper.assert_called_once_with(CONTEXT, 1)

    def test_ordinary_homework_with_unsupported_questions_is_reported_before_model_call(self):
        self.api.open_paper.return_value = meeting_paper(raw_question(kind=3))
        with patch("chapter_tests.ChapterAPI.from_client", return_value=self.api), \
             patch("chapter_tests.AISolver") as solver, self.assertRaises(UnsupportedChapter):
            run_chapter_cli(self.client, "answer", ["course-key"], "student-exam", _batch=True, output=Mock())
        solver.assert_not_called()
        self.api.save_answer.assert_not_called()

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
        self.assertGreaterEqual(output.getvalue().count("选择要处理的测试"), 2)
        self.assertIn("返回课程列表", output.getvalue())
        self.assertIn("返回功能选择", output.getvalue())
        self.assertIn("AI 做题", output.getvalue())
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

    def test_batch_includes_other_homework_and_defaults_to_save_only(self):
        items = [{"id": "other", "examName": "test"}, self.item,
                 {"id": "meeting", "examName": "见面课测验", "meetCourseType": 1},
                 {**self.item, "id": "peer", "meetCourseType": 1, "reviewType": 1}]
        with patch("chapter_tests.atomic_json"), patch("chapter_tests.run_chapter_cli", return_value={"status": "saved", "progress": "fake.json"}) as run:
            result = run_chapter_batch(self.client, self.course, items, {}, output=Mock())
        self.assertEqual([i["status"] for i in result["items"]], ["saved", "saved", "saved", "skipped"])
        self.assertEqual([c.args[3] for c in run.call_args_list], ["other", "paper-1", "meeting"])
        self.assertEqual(result["items"][2]["kind"], "meeting")
        self.assertFalse(run.call_args.kwargs["submit"])
        self.assertEqual(result["status"], "batch_completed")

    def test_batch_with_skipped_work_does_not_claim_everything_completed(self):
        from chapter_tests import chapter_result
        result = {"status": "batch_completed", "course": "课程", "items": [
            {"name": "互评", "kind": "peer", "status": "skipped", "reason": "独立流程"}],
            "remaining": 0, "submit": False, "progress": "fake.json"}
        title, notes = chapter_result(result)
        self.assertIn("有未完成作业", title)
        self.assertIn("跳过 1 份", notes[1])

    def test_empty_selection_displays_normal_result_and_returns(self):
        for action in ("export", "answer"):
            with self.subTest(action=action), \
                 patch("chapter_tests.run_chapter_cli", return_value={"status": "listed", "tests": []}) as run, \
                 patch("chapter_tests.choose_item") as choose, patch("terminal_ui.show_page"), \
                 patch("terminal_ui.show_result_page") as result:
                run_chapter_selection(self.client, self.course, action, {})
                self.assertEqual(result.call_args.args[1], "没有未交测试")
                self.assertIn("当前课程没有未交测试/作业。", result.call_args.args[2])
                choose.assert_not_called()
                run.assert_called_once()

    def test_meeting_is_recognized_with_integer_or_string_marker(self):
        for marker in (1, "1"):
            self.assertEqual(homework_kind({"meetCourseType": marker}), "meeting")
        self.assertEqual(homework_kind({"examName": "见面课测验"}), "other")

    def test_meeting_selection_displays_type_and_defaults_to_save_only(self):
        item = {"id": "meeting", "examName": "茶与健康", "meetCourseType": 1}
        listing = {"status": "listed", "tests": [item]}
        result = {"status": "saved", "exam": "茶与健康", "kind": "meeting", "count": 1,
                  "destination": "fake.json", "progress": "progress.json"}
        keys = iter(["enter", "enter", "enter", "back"])
        with patch("chapter_tests.sys.stdin.isatty", return_value=True), \
             patch("terminal_ui.read_menu_key", side_effect=lambda: next(keys)), \
             patch("chapter_tests.run_chapter_cli", side_effect=[listing, result, listing]) as run, \
             redirect_stdout(io.StringIO()) as output:
            run_chapter_selection(self.client, self.course, "answer", {})
        self.assertIn("[见面课] 茶与健康", output.getvalue())
        self.assertIn("提交后不可重做", output.getvalue())
        self.assertIn("类型：见面课", output.getvalue())
        self.assertFalse(run.call_args_list[1].kwargs["submit"])

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
