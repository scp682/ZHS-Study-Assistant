"""Workbench layout and lifecycle checks using fake courses, with no login."""
import io
import re
import sys
import types
import unittest
from unittest.mock import Mock, patch

from ObjDict import ObjDict
from study_ui import StudyDashboard, preview
from terminal_ui import display_width

test_logger = types.ModuleType("logger")
test_logger.logger = Mock()
with patch.dict(sys.modules, {"logger": test_logger}):
    import fucker


class StudyUiTests(unittest.TestCase):
    def test_layout_fits_console_and_chinese_borders_align(self):
        for columns, rows in ((100, 32), (80, 26), (52, 24), (40, 18)):
            for waiting in (False, True):
                ui = preview(waiting)
                ui.video = "长视频标题" * 30 + "\033[2J\n"
                frame = re.sub(r"\033\[[0-9;?]*[A-Za-z]", "", ui.frame(columns, rows))
                lines = frame.splitlines()
                self.assertLess(len(lines), rows)
                self.assertTrue(all(display_width(line) < columns for line in lines))
                self.assertEqual(len({display_width(line) for line in lines}), 1)
                self.assertIn("等待验证" if waiting else "正在学习", frame)
                self.assertIn("Ctrl+C", frame)

    def test_wait_preserves_progress_and_resume_clears_warning(self):
        ui = preview()
        before = ui.current, ui.target, ui.completed
        ui.update("waiting")
        self.assertTrue(ui.waiting)
        self.assertEqual((ui.current, ui.target, ui.completed), before)
        ui.update("resume")
        self.assertFalse(ui.waiting)
        self.assertIn("继续运行", ui.status)

    def test_exception_restores_client_flags_and_cursor(self):
        client = types.SimpleNamespace(tree_view=True, progressbar_view=False)
        stream = io.StringIO()
        with patch("study_ui.is_terminal", return_value=True):
            with self.assertRaises(KeyboardInterrupt):
                with StudyDashboard(client, output=stream):
                    self.assertFalse(client.tree_view)
                    self.assertFalse(client.progressbar_view)
                    self.assertIsInstance(client._study_ui, StudyDashboard)
                    raise KeyboardInterrupt
        self.assertTrue(client.tree_view)
        self.assertFalse(client.progressbar_view)
        self.assertFalse(hasattr(client, "_study_ui"))
        self.assertIn("\033[?25h", stream.getvalue())

    def test_redirected_output_keeps_legacy_mode(self):
        client = types.SimpleNamespace(tree_view=True, progressbar_view=True)
        stream = io.StringIO()
        with StudyDashboard(client, output=stream):
            self.assertTrue(client.tree_view)
            self.assertFalse(hasattr(client, "_study_ui"))
        self.assertEqual(stream.getvalue(), "")

    def test_course_counters_reset_and_single_video_sets_course(self):
        ui = preview()
        ui.update("video", name="单个视频", course_id="other", course_name="另一门课")
        self.assertEqual(ui.course, "另一门课")
        self.assertEqual(ui.completed + ui.skipped + ui.failed, 0)
        ui.update("complete")
        ui.update("skip")
        ui.update("error", message="资源读取失败")
        self.assertEqual((ui.completed, ui.skipped, ui.failed), (1, 1, 1))

    def test_skipped_shared_video_emits_real_name_without_playing(self):
        client = fucker.Fucker.__new__(fucker.Fucker)
        client._study_ui = preview()
        client._checkCookies = Mock()
        client._checkTimeLimit = Mock()
        client._sessionReady = Mock()
        client.end_thre, client.speed = .91, None
        ctx = ObjDict({"course": {"courseInfo": {"name": "示例课程"}},
                       "videos": {"v": {"name": "已学视频", "studyTotalTime": 90,
                                         "watchState": 1, "videoSec": 100}}})
        client.getZhidaoContext = Mock(return_value=ctx)
        client.watchVideo = Mock()
        client.fuckZhidaoVideo("course", "v")
        self.assertEqual(client._study_ui.course, "示例课程")
        self.assertEqual(client._study_ui.video, "已学视频")
        self.assertEqual(client._study_ui.skipped, 1)
        client.watchVideo.assert_not_called()

    def test_shared_course_ctrl_c_reaches_feature_menu(self):
        client = fucker.Fucker.__new__(fucker.Fucker)
        client._study_ui = preview()
        client.tree_view, client.prefix = False, ""
        client.getZhidaoContext = Mock(return_value=ObjDict({
            "course": {"courseInfo": {"name": "示例课程"}},
            "chapters": {"videoChapterDtos": [{"name": "章节", "videoLessons": [
                {"name": "小节", "videoSmallLessons": [{"name": "视频", "videoId": "v"}]}]}]}}))
        client.fuckZhidaoVideo = Mock(side_effect=KeyboardInterrupt)
        with self.assertRaises(KeyboardInterrupt):
            client.fuckZhidaoCourse("course")

    def test_shared_playback_updates_display_after_existing_report(self):
        client = fucker.Fucker.__new__(fucker.Fucker)
        client._study_ui = preview()
        client._checkCookies = Mock()
        client._checkTimeLimit = Mock()
        client._sessionReady = Mock()
        client.end_thre, client.speed, client.progressbar_view = 1, 1, False
        ctx = ObjDict({"course": {"courseInfo": {"name": "示例课程"}}, "fucked_time": 0,
                       "videos": {"v": {"name": "正在播放", "studyTotalTime": 0,
                                         "watchState": 0, "videoSec": 1, "videoId": "v"}}})
        client.getZhidaoContext = Mock(return_value=ctx)
        client.prelearningNote = Mock(return_value=ObjDict({"studiedLessonDto": {"id": 1}}))
        client.loadVideoPointerInfo = Mock(return_value=ObjDict({"questionPoint": []}))
        client.watchVideo = Mock()
        client.threeDimensionalCourseWare = Mock()
        client.saveDatabaseIntervalTimeV2 = Mock()
        with patch("fucker.time.sleep"), patch("fucker.random", return_value=.5):
            client.fuckZhidaoVideo("course", "v")
        client.saveDatabaseIntervalTimeV2.assert_called_once()
        self.assertEqual(client.saveDatabaseIntervalTimeV2.call_args.args[2:4], (1, 0))
        self.assertEqual((client._study_ui.current, client._study_ui.target), (1, 1))
        self.assertEqual(client._study_ui.completed, 1)

    def test_captcha_updates_panel_without_printing_over_it(self):
        client = fucker.Fucker.__new__(fucker.Fucker)
        ui = client._study_ui = preview()
        client._zhidaoQueryOnce = Mock(side_effect=[fucker.CaptchaException(), {"ok": True}])
        client._pushplus = client._bark = client._feishu = Mock()
        before = ui.current

        def waiting(seconds):
            self.assertEqual(seconds, 15)
            self.assertTrue(ui.waiting)
            self.assertEqual(ui.current, before)

        with patch("fucker.time.sleep", side_effect=waiting), patch("builtins.print") as output:
            client.zhidaoQuery("https://example.invalid", {})
        self.assertFalse(ui.waiting)
        self.assertEqual(ui.current, before)
        output.assert_not_called()


if __name__ == "__main__":
    unittest.main()
