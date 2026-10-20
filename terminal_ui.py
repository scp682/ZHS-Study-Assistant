"""Shared Windows terminal panels, keyboard menus and cancellable forms."""
import os
import shutil
import sys
import unicodedata
from contextlib import contextmanager

RESET = "\033[0m"
BORDER = "\033[38;5;60m"
PANEL = "\033[48;5;234m"
SELECTED = "\033[48;5;24m"
WHITE = "\033[38;5;255m"
MUTED = "\033[38;5;245m"
ACCENT = "\033[38;5;117m"
BACK_CODE = 20


class MenuBack(Exception):
    """Leave the current page without applying its pending changes."""


def is_terminal(stream):
    try:
        return stream is not None and stream.isatty()
    except (AttributeError, OSError, ValueError):
        return False


def read_menu_key():
    import msvcrt
    key = msvcrt.getwch()
    if key in ("\x00", "\xe0"):
        return {"H": "up", "P": "down"}.get(msvcrt.getwch(), "")
    if key == "\x03":
        raise KeyboardInterrupt
    if key in ("\x1b", "\x08"):
        return "back"
    return "enter" if key in ("\r", "\n") else ""


@contextmanager
def terminal_style(stream):
    console = None
    try:
        if os.name == "nt" and is_terminal(stream):
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.GetStdHandle.argtypes = [wintypes.DWORD]
            kernel.GetStdHandle.restype = wintypes.HANDLE
            kernel.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            kernel.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            handle = kernel.GetStdHandle(-11 & 0xFFFFFFFF)
            mode = wintypes.DWORD()
            if not kernel.GetConsoleMode(handle, ctypes.byref(mode)):
                raise ctypes.WinError(ctypes.get_last_error())
            if not kernel.SetConsoleMode(handle, mode.value | 4):
                raise ctypes.WinError(ctypes.get_last_error())
            console = kernel, handle, mode.value
        yield
    finally:
        if console:
            kernel, handle, mode = console
            kernel.SetConsoleMode(handle, mode)


def display_width(text):
    return sum(0 if unicodedata.combining(c) else
               2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in text)


def fit_text(text, width):
    # Never allow user-supplied labels to inject terminal control sequences.
    text = "".join(c for c in str(text) if c.isprintable())
    used, result = 0, []
    for char in text:
        size = display_width(char)
        if used + size > width:
            break
        result.append(char)
        used += size
    return "".join(result) + " " * (width - used)


def build_panel(title, subtitle, options=(), descriptions=(), *, selected=0,
                width=76, compact=False, notes=(), footer="↑ ↓ 切换   Enter 确认   Esc 返回"):
    inside = width - 2

    def row(text="", color=MUTED, background=PANEL):
        body = "  " + fit_text(text, inside - 4) + "  "
        return f"{BORDER}│{background}{color}{body}{RESET}{BORDER}│{RESET}"

    def rule(left, right):
        return f"{BORDER}{left}{'─' * inside}{right}{RESET}"

    lines = [rule("╭", "╮"), row(), row("Z H S   /   P E R S O N A L", ACCENT),
             row(title, WHITE + "\033[1m"), row(subtitle), row(), rule("├", "┤"), row()]
    for note in notes:
        lines.append(row(note))
    if notes:
        lines.append(row())
    for index, option in enumerate(options):
        active = index == selected
        background = SELECTED if active else PANEL
        lines.append(row(("› " if active else "  ") + option,
                         WHITE + ("\033[1m" if active else ""), background))
        if not compact and index < len(descriptions):
            lines.append(row("  " + descriptions[index], ACCENT if active else MUTED, background))
        if index != len(options) - 1:
            lines.append(row())
    lines.extend([row(), rule("├", "┤"), row(footer, ACCENT), row(), rule("╰", "╯")])
    return lines


def render_panel(title, subtitle, options=(), descriptions=(), **kwargs):
    columns, rows = shutil.get_terminal_size((100, 32))
    width = max(12, min(76, columns - 4))
    lines = build_panel(title, subtitle, options, descriptions, width=width,
                        compact=rows < 31 or columns < 54, **kwargs)
    # Drop the optional brand and extra spacing in short consoles.
    if len(lines) >= rows:
        lines = [line for line in lines if "Z H S" not in line]
        blank = fit_text("", width - 2)
        lines = [line for line in lines if blank not in line.replace(PANEL, "").replace(MUTED, "")]
    left = " " * max(0, (columns - width) // 2)
    top = max(0, min(4, (rows - len(lines) - 1) // 3))
    return "\033[H" + "\n" * top + "\n".join(left + line for line in lines) + "\n\033[J"


def select_menu(title, subtitle, options, descriptions=(), *, notes=(),
                key_reader=None, output=None, back_index=None, footer=None):
    stream = output or sys.stdout
    read_key = key_reader or read_menu_key
    selected = 0
    extra = {"footer": footer} if footer else {}
    with terminal_style(stream):
        stream.write("\033[2J\033[H\033[?25l")
        try:
            while True:
                stream.write(render_panel(title, subtitle, options, descriptions,
                                          selected=selected, notes=notes, **extra))
                stream.flush()
                while True:
                    key = read_key()
                    if key == "enter" or (key == "back" and back_index is not None):
                        stream.write("\033[2J\033[H")
                        return back_index if key == "back" else selected
                    if key in ("up", "down"):
                        selected = (selected + (1 if key == "down" else -1)) % len(options)
                        break
        finally:
            stream.write("\033[0m\033[?25h")
            stream.flush()


def show_page(title, subtitle, notes=(), *, footer="Esc 取消返回   /back 返回上一级", output=None):
    stream = output or sys.stdout
    with terminal_style(stream):
        stream.write("\033[2J" + render_panel(title, subtitle, notes=notes, footer=footer))
        stream.flush()


def prompt_field(label, *, hidden=False, input_fn=None):
    """Use Esc in native Windows input, with /back also supported in line input."""
    if input_fn is not None:
        value = input_fn(label)
    elif os.name == "nt" and is_terminal(sys.stdin):
        import msvcrt
        chars = []
        columns = shutil.get_terminal_size((100, 32)).columns
        width = max(12, min(76, columns - 4))
        indent = max(0, (columns - width) // 2) + 3
        sys.stdout.write(" " * indent + label)
        sys.stdout.flush()
        while True:
            char = msvcrt.getwch()
            if char in ("\x1b", "\x03"):
                sys.stdout.write("\n")
                raise MenuBack
            if char in ("\x00", "\xe0"):
                msvcrt.getwch()
                continue
            if char in ("\r", "\n"):
                sys.stdout.write("\n")
                break
            if char == "\b":
                if chars:
                    last = chars.pop()
                    cells = 1 if hidden else display_width(last)
                    sys.stdout.write("\b \b" * cells)
            elif char.isprintable():
                chars.append(char)
                sys.stdout.write("*" if hidden else char)
            sys.stdout.flush()
        value = "".join(chars)
    else:
        if hidden:
            from getpass import getpass
            value = getpass(label)
        else:
            value = input(label)
    if value.strip().lower() == "/back":
        raise MenuBack
    return value
