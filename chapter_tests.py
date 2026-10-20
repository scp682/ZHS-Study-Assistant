"""Shared-course chapter homework with explicit save-only or submit modes.

Protocol checked against the public stuExamWeb scripts on 2026-10-20.
Submission is opt-in and requires a final server readback of every answer.
"""
from base64 import b64decode, b64encode
from dataclasses import asdict, dataclass
from hashlib import sha256
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from urllib.parse import unquote, urlsplit

import requests
from Crypto.Cipher import AES, PKCS1_v1_5
from Crypto.PublicKey import RSA
from Crypto.Util.Padding import pad


API_ROOT = "https://studentexam-api.zhihuishu.com/studentExam/gateway/t/v1/"
KEY_URL = "https://appcomm-user.zhihuishu.com/app-commserv-user/c/has"
# Public verification/encryption key distributed by the official base2 /js/2.js.
PUBLIC_KEY = ("MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQCgfZmpLpPEpEFRKBe+ZjWJUjPe+7qg"
              "7pGqcfN3j2egJ8H2mrKwaEqZEnPnpi2O3hN8HRyaFozDOp8gwZiYfiIZjWy0Jr/FNAii"
              "KYh5bq0GsEn+ieMmRyJg/+i1rqizhvCXvFdrdGhFTw5EBwTpsGdwe1utdlrvIJUAFWj9Yh4qbQIDAQAB")
IV = b"1g3qqdh4jvbskb9x"


class ChapterError(RuntimeError):
    """An operation failed or its outcome could not be verified."""


class UnsupportedChapter(ChapterError):
    """A batch item cannot be handled by the supported text-choice workflow."""


def compact_json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def recover_key(signed, public_key=PUBLIC_KEY):
    """Recover the server's PKCS#1 type-1 signed key envelope with its public key."""
    try:
        key = RSA.import_key(b64decode(public_key))
        signature = b64decode(signed, validate=True)
        if len(signature) != key.size_in_bytes() or int.from_bytes(signature, "big") >= key.n:
            raise ValueError("invalid RSA envelope length/value")
        block = pow(int.from_bytes(signature, "big"), key.e, key.n).to_bytes(key.size_in_bytes(), "big")
        prefix, payload = block.split(b"\x00", 2)[1:]
        if not prefix.startswith(b"\x01") or len(prefix) < 9 or set(prefix[1:]) != {255}:
            raise ValueError("invalid PKCS#1 padding")
        result = json.loads(payload)
        secret = result["cKey"].encode("utf-8")
        if len(secret) not in (16, 24, 32):
            raise ValueError("invalid AES key length")
        return secret
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ChapterError("无法解析官网返回的章节测试密钥，协议可能已变化。") from exc


class ChapterAPI:
    """Use a separate session so homework requests do not change video headers."""
    def __init__(self, session):
        self.session = session
        self.key = None

    @classmethod
    def from_client(cls, client):
        session = requests.Session()
        session.cookies.update(client.cookies)
        session.cookies.update(client.session.cookies)
        session.proxies.update(client.proxies)
        session.headers.update({"User-Agent": client.headers.get("User-Agent", "Mozilla/5.0"),
                                "Origin": "https://onlineexamh5new.zhihuishu.com",
                                "Referer": "https://onlineexamh5new.zhihuishu.com/"})
        return cls(session)

    def authenticate(self):
        """Complete the same CAS service redirect as the official homework page."""
        response = self.session.get(
            "https://studentexam-api.zhihuishu.com/studentExam/gateway/f/v1/gologin/login",
            params={"fromurl": "https://onlineexamh5new.zhihuishu.com/stuExamWeb.html"}, timeout=15)
        response.raise_for_status()
        if urlsplit(response.url).hostname not in ("onlineexamh5new.zhihuishu.com", "studentexam-api.zhihuishu.com"):
            raise ChapterError("章节测试登录已失效，请重新扫码登录后再试。")
        # The current CAS callback may finish on a user-management HTML page.
        # Verify the account through the API instead of treating that URL as failure.
        info = self.request("ulogin/getLoginUserInfo", {}).get("rt")
        try:
            expected = json.loads(unquote(self.session.cookies.get("CASLOGC", ""))).get("uuid")
        except (ValueError, AttributeError, requests.cookies.CookieConflictError) as exc:
            raise ChapterError("无法核对章节测试登录账号。") from exc
        if not isinstance(info, dict) or not expected or str(info.get("uuid")) != str(expected):
            raise ChapterError("章节测试登录账号未确认，请重新登录。")

    @staticmethod
    def decode_response(response):
        response.raise_for_status()
        try:
            data = response.json()
        except ValueError as exc:
            raise ChapterError("接口未返回 JSON，请检查登录状态或官网页面。") from exc
        if not isinstance(data, dict):
            raise ChapterError("章节测试接口返回格式发生变化。")
        code = data.get("code")
        if str(code) == "-12":
            raise ChapterError("需要在官网完成章节测试的人机验证，完成后重新运行即可。")
        if str(data.get("status")) != "200" or (code is not None and str(code) not in ("0", "200")):
            raise ChapterError(f"章节测试接口拒绝请求：{data.get('msg') or data.get('message') or '未知错误'}")
        return data

    def get_key(self):
        if self.key is None:
            public = RSA.import_key(b64decode(PUBLIC_KEY))
            uid = b64encode(PKCS1_v1_5.new(public).encrypt(b'{"module":3}')).decode()
            data = self.decode_response(self.session.get(KEY_URL, params={"uid": uid}, timeout=15))
            result = data.get("rt")
            if not isinstance(result, dict):
                raise ChapterError("官网未返回有效的章节测试密钥。")
            self.key = recover_key(result.get("sl", ""))
        return self.key

    def request(self, endpoint, payload):
        # Endpoint names are internal constants, never arbitrary URLs from a page.
        if endpoint not in {"ulogin/getLoginUserInfo", "exam/getSingleRecruit", "exam/getLoginSchoolInfo", "student/getStudentHomework", "student/doHomework",
                            "answer/hasAnswer", "answer/getStuAnswerInfoNew", "answer/saveStudentAnswer", "answer/submit"}:
            raise ValueError("unsupported chapter endpoint")
        encrypted = AES.new(self.get_key(), AES.MODE_CBC, IV).encrypt(pad(compact_json(payload).encode("utf-8"), 16))
        root = API_ROOT.replace("/t/v1/", "/f/v1/") if endpoint == "ulogin/getLoginUserInfo" else API_ROOT
        response = self.session.post(root + endpoint,
                                     data={"secretStr": b64encode(encrypted).decode()}, timeout=15)
        return self.decode_response(response)

    def list_tests(self, recruit_id, course_id):
        tests, seen = [], set()
        for page in range(100):
            data = self.request("student/getStudentHomework", {
                "recruitId": recruit_id, "courseId": course_id, "flag": 1, "pageSize": 100, "pageNum": page})
            result = data.get("rt")
            if not isinstance(result, dict) or not isinstance(result.get("studentHomeworkList"), list):
                raise ChapterError("测试列表格式发生变化，未继续请求。")
            batch = result["studentHomeworkList"]
            for item in batch:
                item_id = str(item.get("id", ""))
                if not item_id or item_id in seen:
                    raise ChapterError("测试列表缺少标识或分页重复，未继续请求。")
                seen.add(item_id)
                tests.append(item)
            if str(result.get("next")) == "-1":
                return tests
            if not batch:
                raise ChapterError("测试列表分页标识异常。")
        raise ChapterError("测试列表超过分页上限。")

    def open_paper(self, context):
        payload = context.payload()
        payload["studentExamId"] = payload.pop("stuExamId")
        result = self.request("student/doHomework", payload).get("rt")
        if not isinstance(result, dict) or not isinstance(result.get("examBase"), dict):
            raise ChapterError("试卷格式发生变化，未开始答题。")
        return result

    def read_answers(self, context, question_ids):
        if not question_ids:
            return {}
        has_answer = self.request("answer/hasAnswer", {
            "recruitId": context.recruit_id, "examId": context.exam_id,
            "stuExamId": context.student_exam_id}).get("rt")
        if str(has_answer) == "0":
            return {}
        if str(has_answer) != "1":
            raise ChapterError("无法确认试卷是否已有答案。")
        result = self.request("answer/getStuAnswerInfoNew", {
            **context.payload(), "questionIds": ",".join(map(str, question_ids))}).get("rt")
        if not isinstance(result, dict):
            raise ChapterError("已有答案格式发生变化，未继续保存。")
        return result

    def save_answer(self, context, question, answers):
        values = validate_answers(question, answers)
        row = {**context.payload(), "eid": question.id, "deviceId": "", "examType": "", "fromType": 3,
               "answer": ",".join(values), "dataIds": "", "questionType": question.type}
        response = self.request("answer/saveStudentAnswer", {
            "recruitId": context.recruit_id, "stuExamAnswer": compact_json([row])})
        result = response.get("rt")
        if not isinstance(result, dict) or str(result.get("statu")) != "1":
            raise ChapterError("本题未确认保存成功（可能需要验证或试卷已锁定），请检查官网。")

    def submit_paper(self, context, count):
        payload = {"recruitId": context.recruit_id, "examId": context.exam_id,
                   "stuExamId": context.student_exam_id, "achieveCount": count}
        result = self.request("answer/submit", payload).get("rt")
        if not isinstance(result, dict) or str(result.get("statu")) not in ("0", "1"):
            raise ChapterError("交卷接口未确认成功，请在官网检查；不会自动重复交卷。")
        return result


@dataclass(frozen=True)
class PaperContext:
    recruit_id: str
    course_id: str
    student_exam_id: str
    exam_id: str
    school_id: str

    def payload(self):
        return {"recruitId": self.recruit_id, "courseId": self.course_id,
                "stuExamId": self.student_exam_id, "examId": self.exam_id, "schoolId": self.school_id}


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden += 1
        elif tag in ("br", "p", "div", "li"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def plain_text(value):
    parser = PlainText()
    parser.feed(str(value or ""))
    return "".join(parser.parts).strip()


@dataclass
class Question:
    id: str
    type: int
    text: str
    options: list
    has_media: bool = False


def parse_questions(paper):
    """Flatten child questions while retaining the parent reading passage."""
    questions, seen = [], set()

    def visit(raw, parents="", parent_media=False):
        if not isinstance(raw, dict):
            raise ChapterError("题目格式无效。")
        content = raw.get("name", "")
        media = parent_media or bool(raw.get("questionDatas")) or bool(re.search(r"<(?:img|audio|video|svg|object)\b", str(content), re.I))
        stem = "\n\n".join(s for s in (parents, plain_text(content)) if s)
        children = raw.get("questionChildrens")
        if children:
            for child in children:
                visit(child, stem, media)
            return
        question_id = str(raw.get("eid") or "")
        if not question_id or question_id in seen:
            raise ChapterError("试卷题目标识缺失或重复。")
        seen.add(question_id)
        kind = raw.get("questionType")
        try:
            kind = int(kind.get("id") if isinstance(kind, dict) else kind)
        except (TypeError, ValueError) as exc:
            raise ChapterError("试卷题型缺失。") from exc
        options = []
        for option in raw.get("questionOptions") or []:
            value = option.get("content", option.get("name", ""))
            media = media or bool(re.search(r"<(?:img|audio|video|svg|object)\b", str(value), re.I))
            options.append({"id": str(option.get("id", "")), "content": plain_text(value)})
        questions.append(Question(question_id, kind, stem, options, media))

    parts = paper.get("examBase", {}).get("workExamParts")
    if not isinstance(parts, list):
        raise ChapterError("试卷分区格式发生变化。")
    for part in parts:
        if not isinstance(part.get("questionDtos"), list):
            raise ChapterError("试卷题目列表格式发生变化。")
        for question in part["questionDtos"]:
            visit(question)
    if not questions:
        raise ChapterError("未取得题目。")
    return questions


def validate_answers(question, answers):
    if question.type not in (1, 2, 14) or question.has_media:
        raise UnsupportedChapter("当前自动保存仅支持纯文字单选、多选和判断题；此题需人工处理。")
    allowed = {option["id"] for option in question.options}
    if (not question.text or len(allowed) < 2 or "" in allowed or len(allowed) != len(question.options)
            or any(not option["content"] for option in question.options)):
        raise ChapterError("题目或选项内容不完整，未保存答案。")
    if not isinstance(answers, list) or not answers:
        raise ChapterError("AI 未返回有效的选项列表。")
    if any(not isinstance(answer, (str, int)) or isinstance(answer, bool) for answer in answers):
        raise ChapterError("AI 返回的选项标识格式错误。")
    values = [str(answer) for answer in answers]
    if len(set(values)) != len(values) or not set(values) <= allowed:
        raise ChapterError("AI 返回了重复选项或题目中不存在的选项，未保存。")
    if question.type in (1, 14) and len(values) != 1:
        raise ChapterError("单选或判断题必须只选择一项。")
    return values


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                         prefix=".chapter-", delete=False) as output:
            temporary = output.name
            json.dump(data, output, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


class ChapterRunner:
    def __init__(self, api, context, solve, report_path, output=print):
        self.api, self.context, self.solve = api, context, solve
        self.report_path, self.output = Path(report_path), output

    def run(self, paper, *, submit=False):
        if str(paper.get("state")) in ("3", "4"):
            raise ChapterError("试卷已提交或已结束，未修改答案。")
        if not paper.get("examBase", {}).get("chapterId"):
            raise ChapterError("此试卷没有章节标识，未按章节测试自动作答。")
        questions = parse_questions(paper)
        existing = self.api.read_answers(self.context, [q.id for q in questions])
        report = {"context": asdict(self.context), "status": "running", "questions": {}, "submitted": False}
        atomic_json(self.report_path, report)
        for index, question in enumerate(questions, 1):
            row = {"status": "pending", "question": asdict(question)}
            report["questions"][question.id] = row
            try:
                previous = existing.get(question.id, {})
                if not isinstance(previous, dict):
                    raise ChapterError("服务器返回的已有答案格式错误。")
                if str(previous.get("answer") or "").strip():
                    row.update(status="already_saved", answer=previous["answer"])
                else:
                    # Validate eligibility before calling the model.
                    validate_answers(question, [question.options[0]["id"]] if question.options else [])
                    answers = validate_answers(question, self.solve(question))
                    row.update(status="generated", answer=answers)
                    atomic_json(self.report_path, report)
                    # Mark the operation before sending it: a timeout can mean an unknown outcome.
                    row["status"] = "saving"
                    atomic_json(self.report_path, report)
                    self.api.save_answer(self.context, question, answers)
                    row["status"] = "accepted"
                    atomic_json(self.report_path, report)
                    readback = self.api.read_answers(self.context, [question.id]).get(question.id, {})
                    saved = str(readback.get("answer") or "").split(",") if isinstance(readback, dict) else []
                    if set(saved) != set(answers):
                        raise ChapterError("保存接口已接受，但回读答案不一致；已停止，重新运行前请检查官网。")
                    row["status"] = "verified"
                self.output(f"[{index}/{len(questions)}] {question.id}：" +
                            ("服务器已有答案，保留" if row["status"] == "already_saved" else "已保存并回读确认"))
            except (Exception, KeyboardInterrupt) as exc:
                row["previous_status"] = row["status"]
                row["status"] = "failed"
                row["error"] = str(exc) or "用户中止"
                report["status"] = "stopped"
                atomic_json(self.report_path, report)
                if isinstance(exc, KeyboardInterrupt):
                    raise
                raise ChapterError(f"第 {index} 题处理停止：{exc}；进度已记录在 {self.report_path}") from exc
            atomic_json(self.report_path, report)
        report["status"] = "saved"
        atomic_json(self.report_path, report)
        if submit:
            report["submission_status"] = "checking"
            atomic_json(self.report_path, report)
            try:
                final = self.api.read_answers(self.context, [q.id for q in questions])
                for question in questions:
                    row = report["questions"][question.id]
                    expected = row["answer"].split(",") if isinstance(row["answer"], str) else row["answer"]
                    expected = validate_answers(question, expected)
                    saved = final.get(question.id, {})
                    if not isinstance(saved, dict) or set(str(saved.get("answer") or "").split(",")) != set(expected):
                        raise ChapterError("交卷前回读发现答案缺失或变化，已保存但未交卷。")
                problem_count = paper["examBase"].get("problemNum", len(questions))
                if int(problem_count) != len(questions):
                    raise ChapterError("试卷题数与已核对题数不符，已保存但未交卷。")
                report["submission_status"] = "sending"
                atomic_json(self.report_path, report)
                receipt = self.api.submit_paper(self.context, len(questions))
                report["submission_status"] = "accepted"
                atomic_json(self.report_path, report)
                confirmation = self.api.open_paper(self.context)
                if str(confirmation.get("state")) not in ("3", "4"):
                    raise ChapterError("交卷请求已接受，但未读到已提交状态，请到官网核对；不会自动重试。")
                report.update(status="submitted", submitted=True, submission_status="verified",
                              server_state=confirmation["state"])
                if receipt.get("score") is not None:
                    report["score"] = receipt["score"]
            except (Exception, KeyboardInterrupt) as exc:
                uncertain = report["submission_status"] in ("sending", "accepted")
                report.update(status="submission_unconfirmed" if uncertain else "saved",
                              submitted=None if uncertain else False, submission_error=str(exc) or "用户中止")
                atomic_json(self.report_path, report)
                if isinstance(exc, KeyboardInterrupt):
                    raise
                raise ChapterError(f"交卷未确认：{exc}；请检查官网与 {self.report_path}") from exc
            atomic_json(self.report_path, report)
        return report


class AISolver:
    """Reuse the configured model, with strict option-ID validation and no guessing."""
    def __init__(self, client, config, course_name):
        self.client, self.config, self.course_name = client, config, course_name
        self.model = None

    def __call__(self, question):
        if self.model is None:
            if not self.config.get("enabled", False):
                raise ChapterError("AI 尚未启用，请检查 config.json 的 ai 配置。")
            from fucker import Openai
            conf = self.config.get("openai", {})
            use_zhidao = self.config.get("use_zhidao_ai", False)
            if not use_zhidao and (not conf.get("api_key") or conf.get("api_key") == "sk-" or not conf.get("model_name")):
                raise ChapterError("请先填写有效的 AI 服务密钥及模型。")
            self.model = Openai(baseUrl=conf.get("api_base", "https://api.openai.com/v1"),
                                apiKey=conf.get("api_key", ""), modelName=conf.get("model_name", ""),
                                useZhidao=use_zhidao, zhiDaosession=self.client.session,
                                stream=self.config.get("use_stream", False), extra={"courseName": self.course_name})
        methods = {1: self.model.singleChoiceTemplate, 2: self.model.multipleChoiceTemplate,
                   14: self.model.judgementTemplate}
        prompt = methods[question.type](question.text, question.options)
        return validate_answers(question, self.model.generateAnswer(prompt))


def choose_item(title, items, label, *, back_label="返回上一级"):
    from terminal_ui import MenuBack, select_menu
    if not sys.stdin.isatty():
        raise ChapterError("非交互运行时，请使用 -c 指定课程，并使用 --chapter-exam 指定测试。")
    titles = [label(item) for item in items] + [back_label]
    selected = select_menu(title, "↑ ↓ 选择，Enter 确认", titles,
                           back_index=len(items))
    if selected == len(items):
        raise MenuBack
    return items[selected]


def run_chapter_cli(client, action, courses=None, exam_id=None, ai_config=None, *, output=None, show_listing=True,
                    submit=False, all_tests=False, _batch=False):
    """Called after the existing login flow; no video processing is entered."""
    if action == "menu":
        return run_chapter_menu(client, courses, ai_config)
    if (submit or all_tests) and action != "answer":
        raise ChapterError("批量作答与保存并提交仅适用于 AI 作答模式。")
    if all_tests and exam_id:
        raise ChapterError("一键全做不能同时指定单份测试。")
    output = output or print
    available = client.getZhidaoList()
    if courses:
        if len(courses) != 1:
            raise ChapterError("章节测试一次处理一门共享课，请只指定一个课程 ID。")
        matches = [c for c in available if str(c.get("secret")) == str(courses[0])]
        if len(matches) != 1:
            raise ChapterError("指定课程不在当前账号的共享课列表中。")
        course = matches[0]
    else:
        if not available:
            raise ChapterError("当前账号没有可用共享课。")
        course = choose_item("选择章节测试课程", available, lambda c: c.get("courseName", "未命名课程"))
    ctx = client.getZhidaoContext(course["secret"])
    api = ChapterAPI.from_client(client)
    api.authenticate()
    recruit_id = ctx.course.recruitId
    recruit = api.request("exam/getSingleRecruit", {"recruitId": recruit_id}).get("rt")
    if not isinstance(recruit, dict) or not recruit.get("courseId"):
        raise ChapterError("无法取得考试系统中的课程标识。")
    tests = api.list_tests(recruit_id, recruit["courseId"])
    if show_listing:
        output(f"课程：{course['courseName']}；未交测试/作业：{len(tests)}")
        for item in tests:
            output(f"  {item.get('id')}  {item.get('examName', '未命名测试')}")
    if action == "list" or not tests:
        return {"course": course["courseName"], "tests": tests, "status": "listed"}
    if all_tests:
        result = run_chapter_batch(client, course, tests, ai_config, submit=submit, output=output)
        if show_listing:
            subtitle, notes = chapter_result(result)
            for line in [subtitle] + notes:
                output(line)
        return result
    if exam_id:
        matches = [item for item in tests if str(item.get("id")) == str(exam_id)]
        if len(matches) != 1:
            raise ChapterError("指定测试不在该课程的未交列表中。")
        item = matches[0]
    else:
        item = choose_item("选择一份章节测试", tests, lambda t: t.get("examName", "未命名测试"),
                           back_label="返回章节操作")
    if str(item.get("meetCourseType")) == "1" or str(item.get("reviewType")) == "1":
        raise ChapterError("此项是见面课或互评作业，当前章节测试入口不支持。")
    school = api.request("exam/getLoginSchoolInfo", {}).get("rt")
    school_id = (school or {}).get("schoolId") if isinstance(school, dict) else None
    if not school_id or not item.get("examId") or not item.get("id"):
        raise ChapterError("缺少学校或试卷标识，请检查官网的课程与学校信息。")
    context = PaperContext(str(recruit_id), str(item.get("courseId") or recruit["courseId"]),
                           str(item["id"]), str(item["examId"]), str(school_id))
    paper = api.open_paper(context)
    questions = parse_questions(paper)
    if _batch:
        for question in questions:
            validate_answers(question, [question.options[0]["id"]] if question.options else [])
    account = sha256(str(client.uuid).encode()).hexdigest()[:16]
    paper_key = sha256(compact_json(asdict(context)).encode()).hexdigest()[:16]
    folder = Path(__file__).resolve().parent / "chapter_test_data" / account / paper_key
    destination = folder / "questions.json"
    atomic_json(destination, {"course_name": course["courseName"], "exam_name": item.get("examName"),
                              "context": asdict(context), "questions": [asdict(q) for q in questions]})
    output(f"已拉取 {len(questions)} 道题，题目文件：{destination}")
    result = {"course": course["courseName"], "exam": item.get("examName"),
              "count": len(questions), "destination": str(destination), "status": "exported"}
    if action == "export":
        return result
    solver = AISolver(client, ai_config or {}, course["courseName"])
    report = ChapterRunner(api, context, solver, folder / "progress.json", output=output).run(paper, submit=submit)
    output("答案已保存并确认交卷。" if submit else "答案已保存；已有答案保持不变。尚未交卷。")
    result = {**result, "status": "submitted" if submit else "saved", "progress": str(folder / "progress.json")}
    if isinstance(report, dict) and report.get("score") is not None:
        result["score"] = report["score"]
    return result


def run_chapter_batch(client, course, tests, ai_config, *, submit=False, output=print):
    """Run the current course's ordinary chapter tests, journaling each result."""
    account = sha256(str(client.uuid).encode()).hexdigest()[:16]
    course_key = sha256(str(course["secret"]).encode()).hexdigest()[:16]
    path = Path(__file__).resolve().parent / "chapter_test_data" / account / course_key / "batch-progress.json"
    report = {"course": course["courseName"], "mode": "save_submit" if submit else "save_only",
              "status": "running", "items": [], "remaining": 0}
    atomic_json(path, report)
    for index, item in enumerate(tests):
        entry = {"id": str(item.get("id", "")), "name": item.get("examName", "未命名测试")}
        report["items"].append(entry)
        if not item.get("chapterId") or str(item.get("meetCourseType")) == "1" or str(item.get("reviewType")) == "1":
            entry.update(status="skipped", reason="不是普通章节测试")
            atomic_json(path, report)
            continue
        output(f"批量 {index+1}/{len(tests)}：{entry['name']}")
        try:
            result = run_chapter_cli(client, "answer", [course["secret"]], entry["id"], ai_config,
                                     submit=submit, output=output, show_listing=False, _batch=True)
            entry.update(status=result["status"], progress=result["progress"])
        except UnsupportedChapter as exc:
            entry.update(status="skipped", reason=str(exc))
        except (Exception, KeyboardInterrupt) as exc:
            entry.update(status="failed", error=str(exc) or "用户中止")
            report.update(status="stopped", remaining=len(tests)-index-1)
            atomic_json(path, report)
            if isinstance(exc, KeyboardInterrupt):
                raise
            break
        atomic_json(path, report)
    else:
        report["status"] = "completed"
    atomic_json(path, report)
    return {"status": "batch_completed" if report["status"] == "completed" else "batch_stopped",
            "course": course["courseName"], "items": report["items"], "remaining": report["remaining"],
            "progress": str(path), "submit": submit}


ALL_TESTS = "__all_chapters__"


def chapter_result(result):
    """Return user-facing result content for a single test or a batch."""
    if result["status"].startswith("batch_"):
        items = result["items"]
        done = sum(i["status"] in ("saved", "submitted") for i in items)
        skipped = sum(i["status"] == "skipped" for i in items)
        failed = sum(i["status"] == "failed" for i in items)
        notes = [f"课程：{result['course']}", f"完成 {done} 份；跳过 {skipped} 份；失败 {failed} 份；未运行 {result['remaining']} 份。",
                 "模式：保存并提交" if result["submit"] else "模式：仅保存（未交卷）"]
        for item in items:
            state = {"saved": "已保存", "submitted": "已提交", "skipped": "跳过", "failed": "失败"}[item["status"]]
            notes.append(f"{item['name']}：{state}" + (f"（{item.get('reason') or item.get('error')}）" if item.get("reason") or item.get("error") else ""))
        notes += ["批量记录：", result["progress"]]
        return ("批量处理完成" if result["status"] == "batch_completed" else "批量处理已停止"), notes
    subtitle = {"exported": "题目导出完成", "saved": "逐题保存完成", "submitted": "保存并提交完成"}[result["status"]]
    notes = [f"测试：{result['exam']}", f"题目数量：{result['count']}", "题目文件：", result["destination"]]
    if result["status"] in ("saved", "submitted"):
        notes += ["答案已保存；已有答案保持不变。",
                  "服务器已确认交卷。" if result["status"] == "submitted" else "尚未交卷。", result["progress"]]
    if "score" in result:
        notes.append(f"服务器返回分数：{result['score']}")
    return subtitle, notes


def run_chapter_selection(client, course, action, ai_config):
    """Results return to this test list; Esc on the list returns to operations."""
    from terminal_ui import MenuBack, show_page, show_result_page
    def progress(text):
        show_page("章节测试", course["courseName"], (str(text),), footer="处理中 · Ctrl+C 中止并返回")
    while True:
        try:
            progress("正在读取测试列表，请稍候……")
            listing = run_chapter_cli(client, "list", [course["secret"]], output=progress, show_listing=False)
            tests = listing["tests"]
            if not tests:
                show_result_page("章节测试", "没有未交测试", ("当前课程的未交列表为空。",), back_label="返回上一页")
                return
            choices = tests + ([ALL_TESTS] if action == "answer" else [])
            item = choose_item("选择一份章节测试", choices,
                               lambda t: "一键全做（当前课程全部章节）" if t == ALL_TESTS else t.get("examName", "未命名测试"),
                               back_label="返回上一页")
        except (MenuBack, KeyboardInterrupt):
            return
        except Exception as exc:
            show_result_page("章节测试", "读取列表未完成", (str(exc),), back_label="返回上一页")
            return
        submit = False
        if action == "answer":
            try:
                mode = choose_item("选择完成方式", ["save", "submit"],
                                   lambda m: "仅保存（不交卷）" if m == "save" else "保存并提交（核对成功后交卷）",
                                   back_label="返回上一页")
                submit = mode == "submit"
            except (MenuBack, KeyboardInterrupt):
                continue
        try:
            result = run_chapter_cli(client, action, [course["secret"]],
                                    None if item == ALL_TESTS else str(item["id"]), ai_config,
                                    submit=submit, all_tests=item == ALL_TESTS, output=progress, show_listing=False)
            subtitle, notes = chapter_result(result)
            show_result_page("章节测试", subtitle, notes, back_label="返回上一页")
        except MenuBack:
            continue
        except KeyboardInterrupt:
            show_result_page("章节测试", "操作已中止", ("已保存的答案保留；交卷状态不确定时请查看官网。",), back_label="返回上一页")
        except Exception as exc:
            show_result_page("章节测试", "操作未完成", (str(exc),), back_label="返回上一页")


def run_chapter_menu(client, courses=None, ai_config=None):
    """Keep login alive while navigating courses, operations and result pages."""
    from terminal_ui import BACK_CODE, MenuBack, show_page, show_result_page
    while True:
        available = client.getZhidaoList()
        if courses:
            available = [c for c in available if str(c.get("secret")) in map(str, courses)]
        if not available:
            show_result_page("章节测试", "没有可用共享课", ("当前账号或指定范围内没有共享课。",), back_label="返回主菜单")
            return BACK_CODE
        try:
            course = choose_item("选择章节测试课程", available, lambda c: c.get("courseName", "未命名课程"), back_label="返回主菜单")
        except (MenuBack, KeyboardInterrupt):
            return BACK_CODE
        while True:
            try:
                action = choose_item(f"章节测试 · {course['courseName']}", ["list", "export", "answer"],
                                     lambda mode: {"list": "查看未交测试列表", "export": "拉取并导出题目", "answer": "AI 自动答题"}[mode],
                                     back_label="返回课程列表")
            except (MenuBack, KeyboardInterrupt):
                break
            if action != "list":
                run_chapter_selection(client, course, action, ai_config)
                continue
            try:
                show_page("章节测试", course["courseName"], ("正在读取测试信息，请稍候……",), footer="Ctrl+C 中止并返回")
                result = run_chapter_cli(client, "list", [course["secret"]], show_listing=False)
                notes = [f"共 {len(result['tests'])} 项未交测试/作业"] + [f"{i}. {t.get('examName', '未命名测试')}" for i,t in enumerate(result["tests"],1)]
                show_result_page("章节测试", "未交测试列表", notes, back_label="返回上一页")
            except (MenuBack, KeyboardInterrupt):
                continue
            except Exception as exc:
                show_result_page("章节测试", "操作未完成", (str(exc),), back_label="返回上一页")
