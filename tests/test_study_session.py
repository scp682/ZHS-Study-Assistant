"""Feature switching and video scopes with fake clients; no online actions."""
import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

from study_session import run_authenticated_menu, run_video_courses
from terminal_ui import BACK_CODE, SELECTED


class StudySessionTests(unittest.TestCase):
    def test_escape_from_features_does_not_start_any_task(self):
        client = Mock()
        with patch("terminal_ui.read_menu_key", return_value="back"), redirect_stdout(io.StringIO()) as out:
            self.assertEqual(run_authenticated_menu(client), BACK_CODE)
        self.assertIn(SELECTED, out.getvalue())
        self.assertIn("自动刷课", out.getvalue())
        self.assertIn("AI 做题", out.getvalue())
        self.assertEqual(client.mock_calls, [])

    def test_failure_keeps_session_available_for_other_feature(self):
        client = Mock()
        with patch("study_session.select_menu", side_effect=[1, 0, 2]), \
             patch("chapter_tests.run_chapter_cli", side_effect=ValueError("fake offline")), \
             patch("study_session.run_video_courses", return_value=("已结束", [])) as video, \
             patch("study_session.show_page"), patch("study_session.show_result_page") as result:
            self.assertEqual(run_authenticated_menu(client), BACK_CODE)
        self.assertEqual(result.call_args_list[0].args[:2], ("AI 做题", "操作未完成"))
        video.assert_called_once_with(client, None, None, None)
        client.login.assert_not_called()

    def test_repeat_video_run_preserves_course_and_video_scope(self):
        client = Mock()
        courses, videos = ["course"], ["video"]
        for _ in range(2):
            subtitle, _ = run_video_courses(client, courses, videos)
            self.assertEqual(subtitle, "自动刷课已结束")
        self.assertEqual(courses, ["course"])
        self.assertEqual(videos, ["video"])
        self.assertEqual(client.fuckVideo.call_count, 2)
        client.fuckWhatever.assert_not_called()

    def test_video_errors_are_not_reported_as_success(self):
        client = Mock()
        client.fuckCourse.side_effect = ValueError("fake unavailable")
        subtitle, notes = run_video_courses(client, ["course"])
        self.assertIn("未完成", subtitle)
        self.assertIn("fake unavailable", notes[0])

    def test_video_auto_scope_still_uses_existing_client_method(self):
        client = Mock()
        run_video_courses(client)
        client.fuckWhatever.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
