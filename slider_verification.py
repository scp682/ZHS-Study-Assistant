"""One visible slider attempt, followed by manual completion if necessary."""
import base64
import io
import statistics
import time
from dataclasses import dataclass

from PIL import Image, ImageFilter


class SliderNotRecognized(Exception):
    pass


@dataclass
class GapMatch:
    offset: int
    confidence: float


def locate_gap(background_bytes, piece_bytes):
    """Match the transparent piece contour to an edge in the background."""
    background = Image.open(io.BytesIO(background_bytes)).convert("L")
    piece = Image.open(io.BytesIO(piece_bytes)).convert("RGBA")
    if piece.height != background.height:
        raise SliderNotRecognized("拼图和背景比例不一致")
    alpha = piece.getchannel("A")
    mask = alpha.point(lambda value: 255 if value >= 128 else 0)
    bounds = mask.getbbox()
    if not bounds or mask.getextrema()[0] != 0:
        raise SliderNotRecognized("无法识别透明拼图轮廓")
    left, top, right, bottom = bounds
    samples = []
    for y in range(max(1, top), min(piece.height - 1, bottom)):
        for x in range(max(1, left), min(piece.width - 1, right)):
            if mask.getpixel((x, y)) and any(
                    not mask.getpixel((x + dx, y + dy))
                    for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1))):
                samples.append((x, y))
    if len(samples) < 20:
        raise SliderNotRecognized("拼图轮廓信息不足")
    edges = background.filter(ImageFilter.FIND_EDGES).filter(ImageFilter.MaxFilter(3))
    pixels = edges.load()
    candidates = []
    for offset in range(8, background.width - right - 1):
        score = sum(pixels[x + offset, y] for x, y in samples) / len(samples)
        candidates.append((score, offset))
    if not candidates:
        raise SliderNotRecognized("未找到可移动的拼图范围")
    score, offset = max(candidates)
    exclusion = max(5, (right - left) // 8)
    distant_scores = [value for value, x in candidates if abs(x - offset) > exclusion]
    runner_up = max(distant_scores, default=score)
    confidence = (score + 1) / (runner_up + 1)
    if score < 12 or confidence < 1.08 or score < statistics.median(v for v, _ in candidates) + 8:
        raise SliderNotRecognized("缺口位置不够明确，保留手动验证")
    return GapMatch(offset, confidence)


def _image_bytes(page, image):
    source = image.get_attribute("src") or ""
    if source.startswith("data:image/") and ";base64," in source:
        return base64.b64decode(source.split(",", 1)[1])
    if not source.startswith(("https://", "http://")):
        raise SliderNotRecognized("不支持的验证码图片格式")
    response = page.request.get(source, timeout=10000)
    if not response.ok:
        raise SliderNotRecognized("无法读取验证码图片")
    return response.body()


def attempt_slider_once(page):
    """Use rendered image scale and visual feedback throughout one drag."""
    deadline = time.monotonic() + 20
    controls = None
    while time.monotonic() < deadline:
        for frame in page.frames:
            background = frame.get_by_role("img", name="验证码背景", exact=True)
            piece = frame.get_by_role("img", name="验证码滑块", exact=True)
            control = frame.get_by_role("button", name="向右拖动滑块填充拼图", exact=True)
            thumb = control.locator(".yidun_slider")
            if background.is_visible() and piece.is_visible() and thumb.is_visible():
                controls = background, piece, thumb
                break
        if controls:
            break
        page.wait_for_timeout(200)
    if not controls:
        raise SliderNotRecognized("没有出现支持的滑块拼图")
    background, piece, thumb = controls
    background_bytes = _image_bytes(page, background)
    match = locate_gap(background_bytes, _image_bytes(page, piece))
    background_box, piece_box, thumb_box = (
        control.bounding_box() for control in (background, piece, thumb))
    if not all((background_box, piece_box, thumb_box)):
        raise SliderNotRecognized("无法读取滑块位置")
    control_box = control.bounding_box()
    if not control_box:
        raise SliderNotRecognized("无法读取滑动轨道")
    source_width = Image.open(io.BytesIO(background_bytes)).width
    target_delta = (match.offset * background_box["width"] / source_width
                    - (piece_box["x"] - background_box["x"]))
    if not 12 < target_delta < background_box["width"] - piece_box["width"]:
        raise SliderNotRecognized("识别到的缺口不在可移动范围内")
    x = thumb_box["x"] + thumb_box["width"] / 2
    y = thumb_box["y"] + thumb_box["height"] / 2
    travel = control_box["width"] - thumb_box["width"]
    target_x = piece_box["x"] + target_delta
    drag = min(target_delta, travel)
    page.mouse.move(x, y)
    page.mouse.down()
    try:
        for _ in range(8):
            page.mouse.move(x + drag, y, steps=12)
            # Allow the widget's animation frame to update the rendered piece.
            page.wait_for_timeout(100)
            moved_box = piece.bounding_box()
            if not moved_box:
                raise SliderNotRecognized("拖动时无法读取拼图位置")
            error = target_x - moved_box["x"]
            if abs(error) <= 1.5:
                break
            corrected = max(0, min(drag + error, travel))
            if abs(corrected - drag) < 0.5:
                raise SliderNotRecognized("无法将拼图对齐到缺口")
            drag = corrected
        else:
            raise SliderNotRecognized("本次未能将拼图准确对齐")
    finally:
        page.mouse.up()


class CaptchaBrowser:
    def __init__(self, playwright, browser, page):
        self.playwright = playwright
        self.browser = browser
        self.page = page

    def attempt_once(self):
        attempt_slider_once(self.page)

    def wait(self):
        if self.page.is_closed():
            raise RuntimeError("验证窗口已关闭，请重新启动登录")
        self.page.wait_for_timeout(250)

    def close(self):
        try:
            self.browser.close()
        finally:
            self.playwright.stop()


def open_captcha_browser(url):
    """A separate visible browser context; do not touch the user's browser profile."""
    from playwright.sync_api import sync_playwright
    playwright = sync_playwright().start()
    browser = None
    try:
        for channel in ("chrome", "msedge"):
            try:
                browser = playwright.chromium.launch(channel=channel, headless=False)
                break
            except Exception:
                continue
        if browser is None:
            raise RuntimeError("未找到可用的 Chrome 或 Edge")
        page = browser.new_page(viewport={"width": 960, "height": 760})
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        return CaptchaBrowser(playwright, browser, page)
    except BaseException:
        if browser:
            browser.close()
        playwright.stop()
        raise
