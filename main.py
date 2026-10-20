import os
import re
import json
import argparse
import requests
import platform
from functools import partial
from fucker import Fucker
from logger import logger
from ObjDict import ObjDict
from utils import showImage, cookie_jar_to_list
from utils import getConfigPath, getRealPath, versionCmp
from login_profile import choose_login, cookie_account_id, load_profile, save_profile, write_json_atomic
from terminal_ui import BACK_CODE, MenuBack, prompt_field, show_page

DEFAULT_CONFIG = {
    "username": "",
    "password": "",
    "qrlogin": True,
    "save_cookies": True,
    "proxies": {},
    "logLevel": "INFO",
    "tree_view": True,
    "progressbar_view": True,
    "qr_extra": {
        "show_in_terminal": None,
        "ensure_unicode": False
    },
    "image_path":"",
    "pushplus": {
        "enable": False,
        "token": ""
    },
    "bark":{
        "enable": False,
        "token": "https://example.com/xxxxxxxxx"
    },
    "feishu": {
        "enable": False,
        "webhook": "",
        "secret": ""
    },
    "config_version": "1.4.0",
    "ai": {
        "enabled": True,
        "use_zhidao_ai": True,
        "openai": {
            "api_base": "https://api.openai.com",
            "api_key": "sk-",
            "model_name": "claude-3-5-sonnet-20240620"
        },
        "ppt_processing": {
            "provide_to_ai": False,
            "moonShot": {
                "base_url": "https://api.moonshot.cn/v1",
                "api_key": "sk-",
                "delete_after_convert": True
            }
        },
        "use_stream": True
    }
}
# get config or create one if not exist
if os.path.isfile(getConfigPath()):
    with open(getConfigPath(), 'r+', encoding="UTF-8") as f:
        # 不指定编码格式会导致config中可能存在的中文字符乱码
        config = ObjDict(json.load(f), default=None)
        if "config_version" not in config:
            config.config_version = "1.0.0"
        if versionCmp(config.config_version, DEFAULT_CONFIG["config_version"]) < 0:
            new = ObjDict(DEFAULT_CONFIG, default=None)
            if versionCmp(config.config_version, "1.0.1") < 0:
                config.pop("qr_extra", None)
            if versionCmp(config.config_version, "1.3.0") < 0:
                pushplus = config.pop("push", {})
                new.pushplus.update(pushplus)
            config.pop("config_version", None)
            new.update(config)
            config = new
            f.seek(0)
            json.dump(config, f, indent=4)
            f.truncate()
            print("****Config file updated****")
else:
    config = ObjDict(DEFAULT_CONFIG, default=None)
    with open(getConfigPath(), 'w') as f:
        json.dump(config, f, indent=4)

# parse arguments
parser = argparse.ArgumentParser(prog="ZHS Fucker")
parser.add_argument("-c", "--course", type=str, nargs="+",
                    help="CourseId or recruitAndCourseId, can be found in URL")
parser.add_argument("-v", "--videos", type=str, nargs="+",
                    help="Video IDs(fileId in URL, or, videoId found in API response")
parser.add_argument("-u", "--username", type=str,
                    help="if not set anywhere, will be prompted")
parser.add_argument("-p", "--password", type=str,
                    help="If not set anywhere, will be prompted. Be careful, it will be stored in history")
parser.add_argument("-s", "--speed", type=float,
                    help="Video Play Speed, default value is maximum speed found on site")
parser.add_argument("-t", "--threshold", type=float,
                    help="Video End Threshold, above this will be considered finished, overloaded when there are questions left unanswered")
parser.add_argument("-l", "--limit", type=int, default=0,
                    help="Time Limit (in minutes, 0 for no limit), default is 0")
login_options = parser.add_mutually_exclusive_group()
login_options.add_argument("-q", "--qrlogin", action="store_true",
                    help="Use QR Login")
login_options.add_argument("--password-login", action="store_true",
                    help="Use account/password login with manual browser CAPTCHA")
parser.add_argument("-d", "--debug", action="store_true", help="Debug Mode")
parser.add_argument("-f", "--fetch", action="store_true",
                    help="Fetch new course list")
parser.add_argument("--show_in_terminal",
                    action="store_true", help="Show QR in terminal")
parser.add_argument("--proxy", type=str,
                    help="Proxy Config, e.g: http://127.0.0.1:8080")
parser.add_argument("--tree_view", type=bool,
                    help="print the tree progress view of the course")
parser.add_argument("--progressbar_view", type=bool,
                    help="print the progressbar view of the course")
parser.add_argument("--image_path", type=str,
                    help="Image save path, default is empty (do not save)")
parser.add_argument("-ai", "--aicourse", type=str, nargs=2,
                    metavar=('COURSE_ID', 'CLASS_ID'),
                    help="AI Course ID and CLASS ID to fuck aiCourse")

parser.add_argument("--noexam", type=bool,
                    help="Disable AI exam")
parser.add_argument("--chapter-tests", choices=("menu", "list", "export", "answer"),
                    help="Shared-course chapter tests: list, export or AI-answer")
parser.add_argument("--chapter-exam", help="Student exam ID from the chapter-test list")
parser.add_argument("--chapter-all", action="store_true", help="Answer all ordinary chapter tests in one selected course")
parser.add_argument("--chapter-submit", action="store_true", help="Submit each paper after every answer is saved and verified")

args = parser.parse_args()
if args.chapter_tests and (args.aicourse or args.fetch or args.videos):
    parser.error("章节测试不能与 AI课程、获取课程清单或视频模式同时使用")
if args.chapter_exam and not args.chapter_tests:
    parser.error("--chapter-exam 需要与 --chapter-tests 一起使用")
if (args.chapter_all or args.chapter_submit) and args.chapter_tests != "answer":
    parser.error("--chapter-all 和 --chapter-submit 仅用于 --chapter-tests answer")
if args.chapter_all and args.chapter_exam:
    parser.error("--chapter-all 不能与 --chapter-exam 同时使用")

course = args.course
username = args.username or config.username
password = args.password if args.password is not None else ("" if args.username else config.password)
qrlogin = False if args.password_login else (args.qrlogin or config.get("qrlogin", True))
save_cookies = config.save_cookies or False
qr_extra = config.qr_extra or ObjDict(default=None)
show_in_terminal = args.show_in_terminal or config.qr_extra.show_in_terminal
tree_view = args.tree_view or config.tree_view
progressbar_view = args.progressbar_view or config.progressbar_view
image_path = args.image_path or config.image_path
if show_in_terminal is None:
    # Defaults to terminal in Windows
    show_in_terminal = platform.system() == "Windows"
ensure_unicode = qr_extra.ensure_unicode or False
logger.setLevel("DEBUG" if args.debug else (config.logLevel or "WARNING"))
proxies = config.proxies or {}
pushplus_token = config.pushplus.enable and config.pushplus.token or ""
bark_token = config.bark.enable and config.bark.token or ""
feishu_config = config.get("feishu") or {}
feishu_webhook = feishu_config.get("webhook", "") if feishu_config.get("enable") else ""
feishu_secret = feishu_config.get("secret", "") or ""

if logger.getLevel() == "DEBUG":
    print("*****************************\n" +
          "DEBUG MODE ENABLED\n" +
          "SENSITIVE DATA WILL BE LOGGED\n" +
          "*****************************\n")

if args.proxy:  # parse proxy
    match args.proxy.lower().split("://"):
        case ["http" | "https", proxy]:
            proxies["http"] = args.proxy
            proxies["https"] = args.proxy
        case ["socks5", proxy]:
            proxies["socks5"] = args.proxy
        case ["all", proxy]:
            proxies["http"] = args.proxy
            proxies["https"] = args.proxy
            proxies["socks5"] = args.proxy
        case [schema]:
            print(f"*Unsupported proxy type: {schema}")
            exit(1)



cookies_path = getRealPath("./cookies.json")
profile_path = getRealPath("./login_profile.json")
saved_profile = load_profile(profile_path)
try:
    choice = choose_login(saved_profile, username, password, qrlogin,
                          has_cookies=os.path.isfile(cookies_path),
                          explicit_method=args.password_login or args.qrlogin,
                          explicit_credentials=bool(args.username or args.password))
except MenuBack:
    raise SystemExit(BACK_CODE)
username, password, qrlogin = choice.username, choice.password, choice.use_qr

# A new instance also keeps a failed cached login from contaminating a new account.
client_options = dict(proxies=proxies, speed=args.speed, end_thre=args.threshold, limit=args.limit,
                pushplus_token=pushplus_token, bark_token=bark_token, tree_view=tree_view, progressbar_view=progressbar_view, image_path=image_path,
                feishu_webhook=feishu_webhook, feishu_secret=feishu_secret)
fucker = Fucker(**client_options)

cookies_loaded = False
if save_cookies and choice.reuse_cookies and os.path.exists(cookies_path):
    try:
        with open(cookies_path, 'r', encoding="utf-8") as f:
            cookies = json.load(f)
        if saved_profile and saved_profile.get("uuid") != cookie_account_id(cookies):
            raise ValueError("保存的登录状态不属于所选账号")
        fucker.cookies = cookies
        ls = fucker.getZhidaoList()
        if ls:
            fucker.getZhidaoContext(ls[-1].secret)
        if args.chapter_tests:
            from chapter_tests import ChapterAPI
            ChapterAPI.from_client(fucker).authenticate()
        else:
            ls = fucker.getHikeList()
            if ls:
                fucker.getHikeContext(ls[-1].courseId)
            ls = fucker.getZhidaoAiList()
        print("Successfully recovered from saved cookies\n")
        cookies_loaded = True
    except Exception:
        print("已保存的登录状态已失效或不匹配，重新登录当前选择的账号。")
        fucker = Fucker(**client_options)


# first you need to login to get cookies
if not cookies_loaded:
    try:
        if qrlogin:
            callback = partial(
                showImage, show_in_terminal=show_in_terminal, ensure_unicode=ensure_unicode)
            fucker.login(use_qr=True, qr_callback=callback)
        else:
            if not username or not password:
                show_page("账号密码登录", "填写账号信息，登录成功后保存到本机",
                          ("密码输入时显示为星号。", "按 Esc 或输入 /back 可取消，原账号记录不会被替换。"))
            username = username or prompt_field("账号：").strip()
            password = password or prompt_field("密码：", hidden=True)
            fucker.login(username, password, interactive=False)
        print("Login Successful\n")
    except (MenuBack, KeyboardInterrupt):
        raise SystemExit(BACK_CODE)
    except Exception as e:
        show_page("登录未完成", "请检查账号信息或按官网提示完成验证", (str(e),),
                  footer="按任意键返回启动菜单")
        exit(1)

# Only remember credentials after a successful authentication or cookie check.
try:
    if save_cookies:
        write_json_atomic(cookies_path, cookie_jar_to_list(fucker.cookies))
    save_profile(profile_path, username, password, qrlogin, fucker.cookies)
except (OSError, ValueError) as exc:
    print(f"登录成功，但保存登录信息失败：{exc}")

# you can add cookies manually by setting cookies property of a Fucker instance
# notice that cookies of zhihuishu.com expires if you login again in somewhere else
# fucker.cookies = {}

if args.chapter_tests:
    from chapter_tests import run_chapter_cli
    try:
        chapter_outcome = run_chapter_cli(fucker, args.chapter_tests, course, args.chapter_exam, config.get("ai", {}),
                                          submit=args.chapter_submit, all_tests=args.chapter_all)
    except (MenuBack, KeyboardInterrupt):
        raise SystemExit(BACK_CODE)
    except Exception as exc:
        print(f"章节测试处理未完成：{exc}")
        raise SystemExit(1)
    if isinstance(chapter_outcome, dict) and chapter_outcome.get("status") == "batch_stopped":
        raise SystemExit(1)
    raise SystemExit(BACK_CODE if args.chapter_tests == "menu" else 0)

if args.aicourse:
    course_id, class_id = args.aicourse
    try:
        def validate_config(config):
            ai_config = config.get("ai", {})
            
            if not isinstance(ai_config, dict):
                raise ValueError("AI配置不是字典，请检查配置文件")

            if ai_config.get("enabled", False) and ai_config.get("use_zhidao_ai", False):
                validate_openai_config(ai_config.get("openai", {}))
            
            validate_ppt_config(ai_config.get("ppt_processing", {}))

        def validate_openai_config(openai_config):
            if not isinstance(openai_config, dict):
                raise ValueError(f"OpenAI配置不是字典，而是{type(openai_config)}，请检查配置文件")
            
            required_fields = ["api_key", "api_base", "model_name"]
            missing_fields = [field for field in required_fields if not openai_config.get(field)]
            
            if missing_fields:
                raise ValueError(f"OpenAI配置不完整，缺少以下字段：{', '.join(missing_fields)}。请检查配置文件")

        def validate_ppt_config(ppt_config):
            if not isinstance(ppt_config, dict):
                raise ValueError(f"PPT处理配置不是字典，而是{type(ppt_config)}，请检查配置文件")
            
            if ppt_config.get("provide_to_ai", False):
                moonShot_conf = ppt_config.get("moonShot", {})
                required_fields = ["base_url", "api_key"]
                missing_fields = [field for field in required_fields if not moonShot_conf.get(field)]
                
                if missing_fields:
                    raise ValueError(f"PPT处理配置不完整，缺少以下字段：{', '.join(missing_fields)}。请检查配置文件")

        # 使用示例
        try:
            validate_config(config)
        except ValueError as e:
            print(f"配置错误: {e}")
            exit(1)

        if args.noexam:
            no_exam = True
        else:
            no_exam = False

        fucker.fuckAiCourse(course_id, class_id, aiConfig=config.ai, no_exam = no_exam)
    except Exception as e:
        logger.exception(e)
        print(f"Error when fucking AI course {course_id}:\n{e}")
    finally:
        print("AI exam finished")
        exit(0)


exec_list = getRealPath("execution.json")
# fetch course list
if args.fetch:
    with open(exec_list, "w") as f:
        zhidao_ids = [{"name": c.courseName, "id": c.secret}
                      for c in fucker.getZhidaoList()]
        hike_ids = [{"name": c.courseName, "id": str(
            c.courseId)} for c in fucker.getHikeList()]
        json.dump(zhidao_ids + hike_ids, f, indent=4, ensure_ascii=False)
    exit(0)

# get courses from file if not specified
if not course and os.path.isfile(exec_list):
    with open(exec_list, "r") as f:
        try:
            course = [str(c["id"]) for c in json.load(f)]
        except Exception as e:
            print(f"*Failed to load course list from file: {e}")
            exit(1)

# still not found?
if not course:
    fucker.fuckWhatever()
    exit(0)

# auto detect mode
for c in course.copy():
    if args.videos:
        for v in args.videos:
            try:
                fucker.fuckVideo(course_id=c, video_id=v)
                print(f"fucked {v}")
                args.videos.remove(v)
            except Exception:
                pass
    else:
        try:
            fucker.fuckCourse(course_id=c)
            course.remove(c)
        except Exception as e:
            logger.exception(e)
            print(f"Error when fucking course {c}:\n{e}")
if args.videos:
    print(f"*the following videos are not fucked: {args.videos}")

# use fuckCourse method to fuck the entire course
# fucker.fuckCourse(course_id="")

# or if you want to fuck a video, use fuckVideo method
# fucker.fuckVideo(course_id="", file_id="")

# check the source code or README to find more info
