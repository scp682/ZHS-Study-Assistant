"""Arrow-key launcher menu; exit codes 0-4 select an action, 5 means failure."""
import sys
from terminal_ui import (SELECTED, display_width, build_panel as shared_panel,
                         render_panel, select_menu)

OPTIONS = ("账号密码登录 / 使用上次账号", "配置飞书通知",
           "测试飞书通知（发送一条测试消息）", "扫码登录", "退出")
TITLES = ("账号密码登录", "配置飞书通知", "测试飞书通知", "扫码登录", "退出助手")
DESCRIPTIONS = ("使用上次账号，或登录其他账号", "设置机器人地址与签名密钥",
                "发送一条测试消息，确认提醒可达", "使用智慧树 / 知到扫描二维码", "关闭启动菜单")
FOOTER = "↑ ↓ 切换   Enter 确认   Esc / Ctrl+C 退出"


def build_panel(selected, width=76, compact=False):
    return shared_panel("智慧树学习助手", "课程与通知，一处管理", TITLES, DESCRIPTIONS,
                        selected=selected, width=width, compact=compact, footer=FOOTER)


def render_menu(selected):
    return render_panel("智慧树学习助手", "课程与通知，一处管理", TITLES, DESCRIPTIONS,
                        selected=selected, footer=FOOTER)


def select_launcher_menu(*, key_reader=None, output=None):
    return select_menu("智慧树学习助手", "课程与通知，一处管理", TITLES, DESCRIPTIONS,
                       key_reader=key_reader, output=output, back_index=4, footer=FOOTER)


def main():
    try:
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise OSError("请在交互终端中运行一键启动.bat")
        return select_launcher_menu()
    except KeyboardInterrupt:
        return 4
    except OSError as exc:
        print(f"菜单打开失败：{exc}", file=sys.stderr)
        return 5


if __name__ == "__main__":
    raise SystemExit(main())
