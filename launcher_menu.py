"""Arrow-key launcher and completion pages; all shortcuts can return here."""
import sys
from terminal_ui import (SELECTED, display_width, build_panel as shared_panel,
                         render_panel, select_menu)

TITLES = ("账号密码登录", "扫码登录", "配置 AI 做题", "配置飞书通知", "测试飞书通知", "退出助手")
OPTIONS = TITLES
DESCRIPTIONS = ("使用上次账号或更换账号，登录后选择功能", "使用智慧树 / 知到扫码，登录后选择功能",
                "设置做题使用的模型接口与密钥", "设置机器人地址与签名密钥",
                "发送一条测试消息，确认提醒可达", "关闭启动菜单")
EXIT_INDEX = len(TITLES) - 1
FOOTER = "↑ ↓ 切换   Enter 确认   Esc / Ctrl+C 退出"


def build_panel(selected, width=76, compact=False):
    return shared_panel("智慧树学习助手", "统一登录 · 自动刷课 / AI 做题", TITLES, DESCRIPTIONS,
                        selected=selected, width=width, compact=compact, dense=compact, footer=FOOTER)


def render_menu(selected):
    return render_panel("智慧树学习助手", "统一登录 · 自动刷课 / AI 做题", TITLES, DESCRIPTIONS,
                        selected=selected, footer=FOOTER)


def select_launcher_menu(*, key_reader=None, output=None):
    return select_menu("智慧树学习助手", "统一登录 · 自动刷课 / AI 做题", TITLES, DESCRIPTIONS,
                       key_reader=key_reader, output=output, back_index=EXIT_INDEX, footer=FOOTER)


def select_result_menu(code, *, key_reader=None, output=None):
    return select_menu("智慧树学习助手", "本次操作已结束" if code == "0" else "本次操作未完成",
                       ("返回主菜单", "退出助手"), ("继续使用其他功能", "关闭启动菜单"),
                       notes=(f"退出代码：{code}",), back_index=0, key_reader=key_reader, output=output)


def main():
    try:
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise OSError("请在交互终端中运行一键启动.bat")
        if len(sys.argv) == 3 and sys.argv[1] == "--result":
            return select_result_menu(sys.argv[2])
        return select_launcher_menu()
    except KeyboardInterrupt:
        return EXIT_INDEX
    except OSError as exc:
        print(f"菜单打开失败：{exc}", file=sys.stderr)
        return len(TITLES)


if __name__ == "__main__":
    raise SystemExit(main())
