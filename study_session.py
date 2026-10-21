"""Authenticated feature selection using one client and the shared terminal theme."""
import json
from pathlib import Path

from terminal_ui import BACK_CODE, MenuBack, select_menu, show_page, show_result_page
from study_ui import StudyDashboard


def run_video_courses(client, courses=None, videos=None, execution_path=None):
    """Run the existing video methods without consuming the selected course/video lists."""
    selected = list(courses or ())
    if not selected and execution_path and Path(execution_path).is_file():
        selected = [str(c["id"]) for c in json.loads(Path(execution_path).read_text(encoding="utf-8-sig"))]
    if not selected:
        client.fuckWhatever()
        return "自动刷课已结束", ["已完成本次自动刷课流程。"]
    remaining = list(videos or ())
    failures = []
    for course_id in selected:
        if videos:
            for video_id in remaining.copy():
                try:
                    client.fuckVideo(course_id=course_id, video_id=video_id)
                    remaining.remove(video_id)
                except Exception:
                    # A video can belong to another selected course; try it there.
                    continue
        else:
            try:
                client.fuckCourse(course_id=course_id)
            except Exception as exc:
                failures.append(f"课程 {course_id}：{exc}")
    if remaining:
        failures.append("未完成视频：" + "、".join(remaining))
    return ("自动刷课部分未完成", failures) if failures else ("自动刷课已结束", ["已完成本次自动刷课流程。"])


def run_authenticated_menu(client, ai_config=None, courses=None, videos=None, execution_path=None):
    """Returning from either feature keeps the authenticated client alive."""
    from chapter_tests import run_chapter_cli

    while True:
        try:
            selected = select_menu("智慧树学习助手", "登录成功 · 选择要使用的功能",
                                   ("自动刷课", "AI 做题", "返回登录首页"),
                                   ("按课程与视频设置运行", "查看题目、AI 作答，并选择保存或提交", "返回首页，可更换账号或调整配置"),
                                   notes=("本次登录状态共用，切换功能无需重复登录。",), back_index=2)
            if selected == 2:
                return BACK_CODE
        except (MenuBack, KeyboardInterrupt):
            return BACK_CODE
        title = "自动刷课" if selected == 0 else "AI 做题"
        try:
            if selected == 0:
                with StudyDashboard(client):
                    subtitle, notes = run_video_courses(client, courses, videos, execution_path)
                show_result_page(title, subtitle, notes, back_label="返回功能选择")
            else:
                run_chapter_cli(client, "menu", courses, ai_config=ai_config)
        except MenuBack:
            continue
        except KeyboardInterrupt:
            show_result_page(title, "操作已中止", ("可返回功能选择继续使用。",), back_label="返回功能选择")
        except Exception as exc:
            show_result_page(title, "操作未完成", (str(exc),), back_label="返回功能选择")
