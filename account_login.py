"""Current login.zhihuishu.com password flow, with a visible CAPTCHA window.

Protocol checked against the official login page on 2026-10-20. Credentials
stay in Python; the local browser page only collects the official CAPTCHA token.
"""
import base64
import json
import secrets
import webbrowser
from threading import Event, Thread
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import unquote

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad


LOGIN_PAGE = "https://login.zhihuishu.com/"
LOGIN_API = "https://user.zhihuishu.com/zhsuser/login/account"
SERVICE = "https://onlineservice-api.zhihuishu.com/login/gologin"
# Public protocol constants from the official site's WebCrypto implementation.
LOGIN_KEY = b"bdas85990328dfpl"
LOGIN_IV = b"563216db1e1744c7"


class AccountLoginError(Exception):
    pass


def encrypt_login_payload(payload):
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    cipher = AES.new(LOGIN_KEY, AES.MODE_CBC, LOGIN_IV)
    return base64.b64encode(cipher.encrypt(pad(raw, AES.block_size))).decode("ascii")


def captcha_page():
    return """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<base href="https://login.zhihuishu.com/">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>智慧树账号登录验证</title>
<style>body{font:17px system-ui;max-width:620px;margin:60px auto;padding:24px;
background:#f5f7fa;color:#162536}button{padding:10px 18px;cursor:pointer}</style>
</head><body><h2>智慧树登录验证</h2>
<p>验证通过后，终端里的程序会自动继续账号密码登录。</p>
<p>本页只处理验证结果，无需在这里输入账号或密码。</p>
<p id="status" role="status">正在加载官网验证组件……</p>
<div id="use-captcha-container"></div>
<button onclick="window.location.reload()">重新加载验证</button>
<script src="https://login.zhihuishu.com/js/jquery.min.js"></script>
<script src="https://base2.zhihuishu.com/ableCaptcha/assets/yidun-captcha.js"></script>
<script src="https://base2.zhihuishu.com/ableCaptcha/assets/ableCaptchaPCv2Common.js"></script>
<script>
const statusEl = document.getElementById('status');
const completeUrl = window.location.origin + window.location.pathname + '/complete';
let verificationComplete = false;
try {
  if (typeof AbleCaptcha !== 'function') throw new Error('官网验证组件加载失败，请重新加载');
  new AbleCaptcha({source:1,capType:'common',eleCapId:'#use-captcha-container',
    width:'400px',popupTitle:'请完成安全验证',enableClose:0,
    errorFallbackCount:3,defaultFallback:true,
    onLoadAble:function(widget){statusEl.textContent='正在尝试自动处理；未通过时可手动拖动滑块';widget.popUp();},
    onSuccessAble:async function(widget,unused,result){
      const token = result && result.data && result.data.validate;
      if (!token) {statusEl.textContent='未收到验证结果，请重新加载';return;}
      verificationComplete = true;
      try {
        const response = await fetch(completeUrl,{method:'POST',
          headers:{'Content-Type':'application/json'},body:JSON.stringify({validate:token})});
        if (!response.ok) throw new Error('无法将验证结果交回终端，请重新加载');
        statusEl.textContent='验证结果已交回终端，请查看终端中的登录结果。可以关闭本页。';
      } catch(error) {statusEl.textContent=error.message;}
    },
    onErrorAble:function(){statusEl.textContent='验证未完成，请重试或重新加载';},
    onCloseAble:function(){if(!verificationComplete) statusEl.textContent='验证已关闭，可点击重新加载';}
  });
} catch(error) {statusEl.textContent=error.message;}
</script></body></html>"""


def wait_for_login_captcha(*, browser_factory=None):
    """Try the visible slider once; wait for the official success callback."""
    path = "/" + secrets.token_urlsafe(24)
    received = []
    completed = Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Do not record verification tokens or local callback paths.

        def respond(self, status, body=b"", content_type="text/plain; charset=utf-8"):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.headers.get("Host") != host or self.path != path:
                self.respond(404)
                return
            self.respond(200, captcha_page().encode("utf-8"), "text/html; charset=utf-8")

        def do_POST(self):
            if (self.headers.get("Host") != host or self.path != path + "/complete"
                    or self.headers.get("Origin") != origin):
                self.respond(403)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 16384:
                    raise ValueError
                token = json.loads(self.rfile.read(length)).get("validate")
                if not isinstance(token, str) or not token.strip() or len(token) > 8192:
                    raise ValueError
            except (ValueError, AttributeError, UnicodeError):
                self.respond(400)
                return
            received.append(token)
            self.respond(200, b"OK")
            completed.set()

    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        host = f"127.0.0.1:{server.server_port}"
        origin = f"http://{host}"
        url = origin + path
        print("将尝试自动处理滑块；未通过时请在验证窗口中手动完成。按 Ctrl+C 可取消。", flush=True)
        print(f"如果浏览器没有自动打开，请访问：{url}", flush=True)
        worker = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
        worker.start()
        window = None
        try:
            if browser_factory is None:
                from slider_verification import open_captcha_browser
                browser_factory = open_captcha_browser
            try:
                window = browser_factory(url)
            except Exception:
                print("自动验证窗口未能启动，将打开浏览器供手动验证。", flush=True)
                try:
                    webbrowser.open(url)
                except webbrowser.Error:
                    pass
            if window and not completed.is_set():
                try:
                    window.attempt_once()
                except Exception:
                    print("本次未能自动完成滑块，请在窗口中手动完成验证。", flush=True)
            while not completed.is_set():
                if window:
                    window.wait()
                else:
                    completed.wait(0.25)
        finally:
            server.shutdown()
            worker.join(timeout=2)
            if window:
                window.close()
    return received[0]


def login_with_password(session, username, password, captcha_provider=None):
    """Authenticate once; never retry a rejected password automatically."""
    if not username or not password:
        raise AccountLoginError("账号和密码不能为空")
    response = session.get(LOGIN_PAGE, timeout=15)
    response.raise_for_status()
    token = (captcha_provider or wait_for_login_captcha)()
    if not isinstance(token, str) or not token.strip():
        raise AccountLoginError("尚未完成人机验证")
    payload = encrypt_login_payload({"account": username, "password": password, "validate": token})
    response = session.post(LOGIN_API, data=payload, timeout=15, headers={
        "Content-Type": "application/json;charset=UTF-8",
        "Origin": LOGIN_PAGE.rstrip("/"), "Referer": LOGIN_PAGE})
    response.raise_for_status()
    result = response.json()
    if not isinstance(result, dict):
        raise AccountLoginError("登录接口返回了无法识别的结果")
    status = result.get("status")
    if status != 1:
        messages = {
            -1: "登录参数或验证结果无效，请重新启动并完成验证",
            -2: "账号或密码错误，请核对后重新启动",
            -4: "登录尝试过多，请稍后再试",
            -5: "账号需要修改密码，请到官网完成后重试",
            -9: "账号需要短信验证，请先在官网完成验证",
            -12: "账号需要额外安全验证，请先在官网完成验证",
            -13: "账号需要异地登录验证，请先在官网完成验证",
            -16: "账号需要额外安全验证，请先在官网完成验证",
            2: "账号需要完善信息，请先在官网完成",
        }
        raise AccountLoginError(messages.get(status, f"账号登录未完成（状态码 {status}），请在官网检查"))
    info = result.get("rt") or {}
    once_password = info.get("pwd") if isinstance(info, dict) else None
    if not once_password:
        raise AccountLoginError("登录接口未返回登录凭据，请重新登录")
    response = session.get("https://passport.zhihuishu.com/login", timeout=15,
                           params={"pwd": once_password, "service": SERVICE})
    response.raise_for_status()
    # An HTTP 200 alone does not establish that the CAS login succeeded.
    try:
        cas = session.cookies.get("CASLOGC")
        uuid = json.loads(unquote(cas or "")).get("uuid")
    except (ValueError, AttributeError):
        uuid = None
    if not uuid or not session.cookies.get("CASTGC"):
        raise AccountLoginError("未获得完整登录状态，请在官网完成额外验证后重试")
    return session.cookies.copy()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="智慧树独立滑块验证测试，不执行账号登录")
    parser.add_argument("--test-captcha", action="store_true")
    args = parser.parse_args()
    if args.test_captcha:
        wait_for_login_captcha()
        print("官网已返回验证成功。本次测试没有执行账号登录。")
    else:
        parser.print_help()
