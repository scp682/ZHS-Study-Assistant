"""Remember the login locally, with plaintext passwords as requested by the user."""
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote
from terminal_ui import MenuBack, read_menu_key as _read_menu_key, select_menu


def write_json_atomic(path, data):
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                         prefix=".login-profile-", delete=False) as output:
            temporary = output.name
            json.dump(data, output, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def cookie_account_id(cookies):
    try:
        if isinstance(cookies, list):
            raw = next(c["value"] for c in cookies if c.get("name") == "CASLOGC")
        else:
            raw = cookies.get("CASLOGC", "")
        return str(json.loads(unquote(raw)).get("uuid") or "")
    except (ValueError, TypeError, AttributeError, KeyError, StopIteration):
        return ""


def load_profile(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if (not isinstance(data, dict) or data.get("version") != 1
                or data.get("method") not in ("qr", "password")
                or not isinstance(data.get("username", ""), str)
                or not isinstance(data.get("password", ""), str)
                or not isinstance(data.get("uuid", ""), str)):
            raise ValueError
    except FileNotFoundError:
        return None
    except (ValueError, OSError):
        print("保存的账号记录无法读取，将使用当前配置或重新登录。")
        return None
    return data


def save_profile(path, username, password, use_qr, cookies):
    uuid = cookie_account_id(cookies)
    if not uuid:
        raise ValueError("没有有效账号标识，未保存账号记录")
    write_json_atomic(path, {"version": 1, "method": "qr" if use_qr else "password",
                             "username": "" if use_qr else username,
                             "password": "" if use_qr else password, "uuid": uuid})


@dataclass
class LoginChoice:
    username: str
    password: str
    use_qr: bool
    reuse_cookies: bool


def select_login_menu(label, *, key_reader=None, output=None):
    """Use the same panel and controls as the launcher, with a back action."""
    return select_menu("账号登录", f"上次账号：{label}",
                       ("使用上次登录账号登录", "更换账号", "返回上一级"),
                       ("优先复用有效的登录状态", "输入新账号，登录成功后更新记录", "回到智慧树学习助手"),
                       key_reader=key_reader or _read_menu_key, output=output, back_index=2)


def choose_login(saved, username, password, use_qr, *, has_cookies=False,
                 explicit_method=False, explicit_credentials=False, key_reader=None):
    """Ask before touching saved cookies; a switch never inherits old credentials."""
    if explicit_credentials:
        return LoginChoice(username, password, use_qr, False)
    if not saved and not has_cookies:
        return LoginChoice(username, password, use_qr, False)
    selected_method = use_qr if explicit_method or not saved else saved["method"] == "qr"
    label = (saved or {}).get("username") or "上次登录账号"
    if label.isdigit() and len(label) == 11:
        label = label[:3] + "****" + label[-4:]
    try:
        selected = select_login_menu(label, key_reader=key_reader)
    except KeyboardInterrupt:
        raise MenuBack from None
    if selected == 2:
        raise MenuBack
    if selected == 1:
        return LoginChoice("", "", selected_method, False)
    if saved:
        return LoginChoice(saved.get("username", ""), saved.get("password", ""),
                           selected_method, True)
    return LoginChoice(username, password, use_qr, True)
