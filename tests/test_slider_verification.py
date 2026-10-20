"""Synthetic images only: do not contact or solve a live CAPTCHA in tests."""
import io
import queue
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlsplit

import requests
from PIL import Image, ImageDraw

import account_login
from slider_verification import SliderNotRecognized, locate_gap, attempt_slider_once


def png(image):
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def puzzle(offsets):
    mask = Image.new("L", (64, 180), 0)
    draw = ImageDraw.Draw(mask)
    draw.rectangle((10, 60, 48, 98), fill=255)
    draw.ellipse((21, 51, 36, 68), fill=255)
    draw.ellipse((40, 73, 57, 88), fill=255)
    draw.ellipse((22, 89, 36, 105), fill=0)
    background = Image.new("RGB", (320, 180), (190, 190, 190))
    for offset in offsets:
        background.paste((35, 35, 35), (offset, 0), mask)
    piece = Image.new("RGBA", mask.size, (190, 190, 190, 0))
    piece.putalpha(mask)
    return png(background), png(piece)


class SliderRecognitionTests(unittest.TestCase):
    def test_recognizes_piece_offset_at_original_image_scale(self):
        for offset in (45, 130, 210):
            with self.subTest(offset=offset):
                match = locate_gap(*puzzle([offset]))
                self.assertLessEqual(abs(match.offset - offset), 2)
                self.assertGreater(match.confidence, 1.08)

    def test_ambiguous_matching_slots_are_left_for_manual_completion(self):
        with self.assertRaises(SliderNotRecognized):
            locate_gap(*puzzle([70, 200]))

    def test_no_gap_does_not_trigger_a_guessed_drag(self):
        with self.assertRaises(SliderNotRecognized):
            locate_gap(*puzzle([]))

    def test_opaque_piece_is_not_treated_as_a_valid_contour(self):
        background, _ = puzzle([130])
        opaque = png(Image.new("RGBA", (64, 180), (100, 100, 100, 255)))
        with self.assertRaises(SliderNotRecognized):
            locate_gap(background, opaque)

    def test_drag_corrects_piece_offset_instead_of_assuming_mouse_distance(self):
        background_bytes, piece_bytes = puzzle([130])
        background, piece, control, thumb = Mock(), Mock(), Mock(), Mock()
        background.is_visible.return_value = piece.is_visible.return_value = thumb.is_visible.return_value = True
        background.bounding_box.return_value = {"x": 100, "y": 100, "width": 320, "height": 180}
        control.bounding_box.return_value = {"x": 100, "y": 300, "width": 320, "height": 40}
        thumb.bounding_box.return_value = {"x": 100, "y": 300, "width": 40, "height": 40}
        control.locator.return_value = thumb
        state = {"held": False, "offset": 0}
        piece.bounding_box.side_effect = lambda: {"x": 100 + state["offset"], "y": 100, "width": 64, "height": 180}
        frame, page = Mock(), Mock()
        frame.get_by_role.side_effect = lambda role, name, exact: {
            "验证码背景": background, "验证码滑块": piece,
            "向右拖动滑块填充拼图": control}[name]
        page.frames = [frame]
        page.mouse.down.side_effect = lambda: state.update(held=True)

        def move(x, y, **kwargs):
            if state["held"]:
                state["offset"] = max(0, (x - 120 - 18) * 0.8)
        page.mouse.move.side_effect = move
        with patch("slider_verification._image_bytes", side_effect=[background_bytes, piece_bytes]):
            attempt_slider_once(page)
        expected = locate_gap(background_bytes, piece_bytes).offset
        self.assertLessEqual(abs(state["offset"] - expected), 1.5)
        page.mouse.down.assert_called_once()
        page.mouse.up.assert_called_once()

    def test_failed_auto_attempt_waits_for_official_callback_and_closes_window(self):
        urls, results = queue.Queue(), queue.Queue()
        window = Mock()
        window.attempt_once.side_effect = SliderNotRecognized("synthetic uncertainty")
        pause = threading.Event()
        window.wait.side_effect = lambda: pause.wait(0.01)

        def factory(url):
            urls.put(url)
            return window

        def run():
            try:
                results.put(account_login.wait_for_login_captcha(browser_factory=factory))
            except BaseException as exc:
                results.put(exc)

        with patch("builtins.print"):
            worker = threading.Thread(target=run, daemon=True)
            worker.start()
            url = urls.get(timeout=5)
            self.assertTrue(results.empty())
            parsed = urlsplit(url)
            local = requests.Session()
            local.trust_env = False
            try:
                response = local.post(url + "/complete", json={"validate": "fake-official-token"},
                                      headers={"Origin": f"{parsed.scheme}://{parsed.netloc}"}, timeout=3)
            finally:
                local.close()
                pause.set()
            self.assertEqual(response.status_code, 200)
            worker.join(timeout=5)
            self.assertFalse(worker.is_alive())
            self.assertEqual(results.get(timeout=1), "fake-official-token")
        window.attempt_once.assert_called_once()
        window.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
