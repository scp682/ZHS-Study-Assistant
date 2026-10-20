"""Configure/test a Feishu bot independently of accounts, cookies, and logs."""
import argparse
import json
from pathlib import Path

from login_profile import write_json_atomic
from push import NotificationError, feishupusher, validate_feishu_webhook
from terminal_ui import BACK_CODE, MenuBack, prompt_field, select_menu, show_page


def read_config(path):
    if not Path(path).exists():
        return {}
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("config.json 应为 JSON 对象")
    return config


def setup_feishu(path, *, input_fn=None, quiet=False):
    prompt = input_fn or input
    if not quiet:
        print("在飞书群中添加「自定义机器人」，复制其 Webhook 地址。")
        print("官方说明：https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot")
    webhook = prompt("粘贴 Webhook 地址：").strip()
    validate_feishu_webhook(webhook)
    secret = prompt("签名密钥（未启用签名校验就直接回车）：").strip()
    config = read_config(path)
    config["feishu"] = {"enable": True, "webhook": webhook, "secret": secret}
    write_json_atomic(path, config)
    if not quiet:
        print("飞书通知配置已保存。重启学习程序后生效。")
        print("在「一键启动.bat」菜单用 ↑ / ↓ 选中「测试飞书通知」，按回车发送测试消息。")


def check_feishu(path):
    config = read_config(path).get("feishu") or {}
    if not config.get("enable"):
        print("飞书通知：未启用。请在「一键启动.bat」菜单用 ↑ / ↓ 选中「配置飞书通知」，按回车。")
        return False
    validate_feishu_webhook(config.get("webhook", ""))
    print("飞书通知：已启用，Webhook 格式有效。此检查不发送消息。")
    return True


def test_feishu(path, *, quiet=False):
    config = read_config(path).get("feishu") or {}
    if not config.get("enable"):
        raise NotificationError("请先在「一键启动.bat」菜单选中「配置飞书通知」并按回车")
    feishupusher("智慧树学习提醒", "这是一条测试通知。收到这条消息说明飞书机器人通知已连通。",
                 config.get("webhook", ""), config.get("secret", "") or "")
    if not quiet:
        print("飞书接口已接受测试通知，请查看机器人所在群。")


def run_notification_page(path, action, *, key_reader=None, field_reader=None):
    """A complete submenu, including cancel, retry and result pages."""
    setup = action == "setup"
    title = "配置飞书通知" if setup else "测试飞书通知"
    subtitle = "连接你的飞书群机器人" if setup else "确认提醒通道是否可用"
    notes = (("准备好机器人的 Webhook 地址和可选签名密钥。",
              "中途取消不会覆盖原来的配置。") if setup else
             ("将向已配置的飞书群发送一条测试消息。",
              "请在飞书群中确认是否收到提醒。"))
    try:
        while True:
            choice = select_menu(title, subtitle,
                                 ("开始配置" if setup else "发送测试通知", "返回上一级"),
                                 ("填写机器人信息" if setup else "发送一次测试消息", "回到启动菜单"),
                                 notes=notes, key_reader=key_reader, back_index=1)
            if choice == 1:
                return BACK_CODE
            try:
                if setup:
                    def read_field(label):
                        secret = "密钥" in label
                        show_page(title, "第 2 步 / 2 · 签名密钥" if secret else "第 1 步 / 2 · 机器人地址",
                                  ("未启用签名校验时，直接按回车跳过。" if secret else
                                   "在飞书群中添加自定义机器人，复制完整 HTTPS Webhook。",
                                   "按 Esc 或输入 /back 返回，尚未保存的内容会丢弃。"))
                        return prompt_field(label, hidden=secret, input_fn=field_reader)
                    setup_feishu(path, input_fn=read_field, quiet=True)
                    result = ("配置已保存", "启动学习程序后生效；已运行的程序需要重启。",
                              "可回到主菜单，打开测试飞书通知检查接收情况。")
                else:
                    show_page(title, "正在发送测试消息，请稍候……",
                              ("正在等待飞书接口响应。",), footer="发送完成后显示结果")
                    test_feishu(path, quiet=True)
                    result = ("飞书接口已接受消息", "请查看机器人所在群，确认消息是否收到。",
                              "手机弹窗提醒还取决于飞书通知和群静音设置。")
            except (NotificationError, OSError, ValueError) as exc:
                choice = select_menu(title, "操作未完成",
                                     ("重新尝试", "返回上一级"), ("重新打开本页", "回到启动菜单"),
                                     notes=(str(exc), "原有账号信息和登录状态不受影响。"),
                                     key_reader=key_reader, back_index=1)
                if choice == 1:
                    return BACK_CODE
                continue
            select_menu(title, result[0], ("返回上一级",), notes=result[1:],
                        key_reader=key_reader, back_index=0)
            return BACK_CODE
    except (MenuBack, KeyboardInterrupt):
        return BACK_CODE


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="学习验证提醒的飞书配置与测试")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--setup-feishu", action="store_true")
    mode.add_argument("--test-feishu", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args()
    path = Path(__file__).resolve().with_name("config.json")
    try:
        if args.setup_feishu or args.test_feishu:
            raise SystemExit(run_notification_page(path, "setup" if args.setup_feishu else "test"))
        else:
            check_feishu(path)
    except (NotificationError, OSError, ValueError) as exc:
        print(f"未完成：{exc}")
        raise SystemExit(1)
