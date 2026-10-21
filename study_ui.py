"""Live study display; presentation only, with no account or network access."""
import shutil
import sys
import time
from collections import deque

from terminal_ui import (ACCENT, BORDER, MUTED, PANEL, RESET, WHITE,
                         fit_text, is_terminal, terminal_style)

GREEN = "\033[38;5;114m"
AMBER = "\033[38;5;221m"


def duration(seconds):
    seconds = max(0, int(seconds))
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}" if hours else f"{minutes:02}:{seconds:02}"


class StudyDashboard:
    """Temporarily replace legacy console output while the interactive task runs."""

    def __init__(self, client, output=None):
        self.client = client
        self.output = output if output is not None else sys.stdout
        self.enabled = is_terminal(self.output)
        self.started = time.monotonic()
        self.course = "正在读取课程…"
        self.chapter = "等待课程目录"
        self.video = "等待开始"
        self.status = "准备中"
        self.current = self.target = 0
        self.speed = 1
        self.total = self.completed = self.skipped = self.failed = 0
        self.events = deque(["正在获取课程与学习记录"], maxlen=3)
        self.waiting = False
        self.course_id = None
        self._saved = {}

    def __enter__(self):
        if not self.enabled:
            return self
        self._style = terminal_style(self.output)
        self._style.__enter__()
        try:
            for name, value in (("_study_ui", self), ("tree_view", False), ("progressbar_view", False)):
                self._saved[name] = (name in vars(self.client), vars(self.client).get(name))
                setattr(self.client, name, value)
            self.output.write("\033[2J\033[H\033[?25l")
            self.render()
        except BaseException:
            self.__exit__(*sys.exc_info())
            raise
        return self

    def __exit__(self, *exc):
        if not self.enabled:
            return
        try:
            for name, (existed, value) in self._saved.items():
                if existed:
                    setattr(self.client, name, value)
                else:
                    delattr(self.client, name)
            self.output.write(RESET + "\033[?25h\n")
            self.output.flush()
        finally:
            self._style.__exit__(*exc)

    def update(self, event, **data):
        if event == "course":
            self.course_id = data.get("course_id")
            self.course = data["name"]
            self.total = data.get("total", 0)
            self.completed = self.skipped = self.failed = 0
            self.current = self.target = 0
            self.video = "等待开始"
            self.chapter = "正在读取目录"
            self.status = "读取课程"
            self.events.clear()
            self.events.append("已载入课程，准备处理学习资源")
        elif event == "chapter":
            self.chapter = data["name"]
        elif event == "video":
            if data.get("course_id") is not None and data["course_id"] != self.course_id:
                self.update("course", course_id=data["course_id"],
                            name=data.get("course_name") or f"课程 {data['course_id']}")
                self.chapter = "指定视频范围"
            self.video = data.get("name") or (self.video if self.video != "等待开始" else "当前视频")
            self.current, self.target = data.get("current", 0), data.get("target", 0)
            self.speed = data.get("speed", 1)
            self.status = "正在学习"
        elif event == "progress":
            self.current, self.target = data["current"], data["target"]
            self.status = data.get("status", "正在学习")
        elif event in ("complete", "skip", "error"):
            if event == "complete":
                self.completed += 1
            elif event == "skip":
                self.skipped += 1
            else:
                self.failed += 1
            self.status = {"complete": "资源处理完成", "skip": "跳过已学资源", "error": "资源处理异常"}[event]
            self.events.append(data.get("message", self.status + " · " + str(self.video)))
        elif event == "waiting":
            self.waiting = True
            self.status = "等待验证 · 已暂停"
            self.events.append("请在智慧树 / 知到完成官方验证，15 秒后重试")
        elif event == "resume":
            self.waiting = False
            self.status = "验证通过 · 继续运行"
            self.events.append("请求已恢复，继续当前进度")
        elif event == "notice":
            self.status = data["message"]
            self.events.append(data["message"])
        self.render()

    def frame(self, columns=100, rows=32):
        """Render one bounded frame using the same palette as the launcher."""
        width = max(12, min(86, columns - 4))
        content = width - 6
        lines = []

        def rule(left, right):
            lines.append(f"{BORDER}{left}{'─' * (width - 2)}{right}{RESET}")

        def row(text="", color=MUTED):
            lines.append(f"{BORDER}│{PANEL}{color}  {fit_text(text, content)}  {RESET}{BORDER}│{RESET}")

        rule("╭", "╮")
        row("Z H S   /   S T U D Y", ACCENT)
        row("自动刷课   ·   学习工作台", WHITE + "\033[1m")
        rule("├", "┤")
        row("当前课程", MUTED)
        row(self.course, WHITE)
        row(self.chapter)
        rule("├", "┤")
        row(self.status, AMBER if self.waiting or self.failed else GREEN)
        row(self.video, WHITE)
        ratio = max(0, min(1, self.current / self.target)) if self.target > 0 else 0
        bar_width = max(1, content - 9)
        filled = int(bar_width * ratio)
        row("█" * filled + "░" * (bar_width - filled) + f"  {ratio:5.1%}", ACCENT)
        if self.target > 0:
            row(f"播放 {duration(self.current)} / 目标 {duration(self.target)}   ·   {self.speed:g} 倍速")
        else:
            row("等待视频进度")
        if rows >= 22:
            row(f"本次运行 {duration(time.monotonic() - self.started)}   ·   进度按本次播放目标显示")
        rule("├", "┤")
        processed = self.completed + self.skipped + self.failed
        row(f"本课已处理 {processed}" + (f" / {self.total}" if self.total else ""), WHITE)
        row(f"完成 {self.completed}   ·   已学跳过 {self.skipped}   ·   异常 {self.failed}")
        if rows >= 26:
            row()
            row("最近动态", ACCENT)
            for message in self.events:
                row("· " + message)
        if self.waiting and rows >= 22:
            row("请到官方页面或手机端完成验证，通过后自动继续。", AMBER)
        rule("├", "┤")
        row("Ctrl+C 中止并返回功能选择", ACCENT)
        rule("╰", "╯")
        # Very short consoles retain the essential course / video / status rows.
        if len(lines) > rows - 1:
            lines = lines[:max(1, rows - 4)] + lines[-3:]
        left = " " * max(0, (columns - width) // 2)
        return "\033[H" + "\n".join(left + line for line in lines) + "\033[J"

    def render(self):
        if self.enabled:
            columns, rows = shutil.get_terminal_size((100, 32))
            self.output.write(self.frame(columns, rows))
            self.output.flush()


def preview(waiting=False):
    """Deterministic demo data, safe to open without logging in."""
    ui = StudyDashboard(None)
    ui.enabled = False
    ui.course = "跨文化英语"
    ui.chapter = "第二章 文化与交流 / 2.3 跨文化沟通"
    ui.video = "文化差异与日常交流"
    ui.current, ui.target, ui.speed = 438, 720, 1.5
    ui.total, ui.completed, ui.skipped = 24, 5, 8
    ui.events = deque(["已学跳过 · 语言与文化", "处理完成 · 沟通中的文化视角", "开始学习 · 文化差异与日常交流"], maxlen=3)
    ui.status = "正在学习"
    if waiting:
        ui.update("waiting")
    return ui


if __name__ == "__main__":
    ui = preview("--waiting" in sys.argv)
    ui.enabled = True
    with terminal_style(sys.stdout):
        sys.stdout.write("\033[2J")
        ui.render()
        sys.stdout.write("\n")
