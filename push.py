'''
Author: Vincent Young, jiajiu123
Date: 2023-03-30 00:15:22
LastEditors: Vincent Young
LastEditTime: 2023-03-30 02:40:33
FilePath: /fuckZHS/push.py
Telegram: https://t.me/missuo(Vincent Young)

Copyright © 2023 by Vincent, All Rights Reserved. 
'''
import requests
import base64
import hashlib
import hmac
import time
from urllib.parse import quote, urlsplit


class NotificationError(Exception):
    pass


def validate_feishu_webhook(webhook):
    if not isinstance(webhook, str):
        raise NotificationError("飞书 Webhook 必须是地址字符串")
    parsed = urlsplit(webhook)
    prefix = "/open-apis/bot/v2/hook/"
    if (parsed.scheme != "https" or parsed.netloc != "open.feishu.cn"
            or not parsed.path.startswith(prefix) or not parsed.path[len(prefix):]
            or parsed.query or parsed.fragment):
        raise NotificationError("请填写飞书自定义机器人的完整 HTTPS Webhook 地址")


def feishupusher(title: str, content, webhook: str, secret: str = "") -> None:
    """Send a text message using Feishu's documented custom bot protocol."""
    validate_feishu_webhook(webhook)
    payload = {"msg_type": "text", "content": {"text": f"{title}\n{content}"}}
    if secret:
        timestamp = str(int(time.time()))
        key = f"{timestamp}\n{secret}".encode("utf-8")
        payload.update(timestamp=timestamp,
                       sign=base64.b64encode(hmac.new(key, b"", hashlib.sha256).digest()).decode("ascii"))
    try:
        response = requests.post(webhook, json=payload, timeout=10, allow_redirects=False)
        response.raise_for_status()
        if 300 <= response.status_code < 400:
            raise NotificationError("飞书通知返回了重定向，未发送成功")
        result = response.json()
    except (requests.RequestException, ValueError):
        # Request exceptions may contain the full secret Webhook URL.
        raise NotificationError("飞书通知发送失败，请检查网络与 Webhook 配置") from None
    if not isinstance(result, dict):
        raise NotificationError("飞书返回了无法识别的结果")
    code = result.get("code", result.get("StatusCode"))
    if code != 0 or isinstance(code, bool):
        raise NotificationError(f"飞书未接受通知（错误码 {code}），请检查机器人安全设置")

def pushpluser(title: str, content, token: str) -> None:
    response = requests.get("https://www.pushplus.plus/send",
                            params={"token": token, "title": title, "content": str(content)}, timeout=10)
    response.raise_for_status()


def barkpusher(title: str, content, token: str) -> None:
    response = requests.get(f"{token.rstrip('/')}/{quote(title, safe='')}/{quote(str(content), safe='')}",
                            timeout=10)
    response.raise_for_status()
