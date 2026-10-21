"""Local, masked AI-key settings window; never prints the API key."""
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

from chapter_tests import atomic_json


CONFIG_PATH = Path(__file__).resolve().parent / "config.json"


def save_settings(path, base_url, model, api_key):
    base_url, model, api_key = base_url.strip().rstrip("/"), model.strip(), api_key.strip()
    parsed = urlsplit(base_url)
    if (not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
            or (parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1", "::1")))):
        raise ValueError("请填写有效的 HTTPS 接口地址（本机接口可以使用 HTTP），不要在地址中包含密钥。")
    if not model or not api_key:
        raise ValueError("请填写模型名称和 API 密钥。")
    path = Path(path)
    config = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
    if not isinstance(config, dict):
        raise ValueError("原配置文件格式错误，未覆盖。")
    ai = dict(config.get("ai") or {})
    provider = dict(ai.get("openai") or {})
    provider.update(api_base=base_url, model_name=model, api_key=api_key)
    ai.update(enabled=True, use_zhidao_ai=False, use_stream=False, openai=provider)
    config["ai"] = ai
    atomic_json(path, config)


def run_ai_settings_page(path=CONFIG_PATH, *, key_reader=None, field_reader=None):
    """Use the same cancellable terminal form and result panels as notifications."""
    from terminal_ui import BACK_CODE, MenuBack, prompt_field, select_menu, show_page, show_result_page
    while True:
        try:
            choice = select_menu("配置 AI 做题", "设置独立运行的模型接口",
                                 ("开始配置", "返回上一级"), ("接口、模型与隐藏输入的密钥", "返回智慧树学习助手"),
                                 key_reader=key_reader, back_index=1)
            if choice == 1:
                return BACK_CODE
            config = json.loads(Path(path).read_text(encoding="utf-8-sig")) if Path(path).exists() else {}
            previous = (config.get("ai") or {}).get("openai") or {}
            previous_base = previous.get("api_base") or "https://api.deepseek.com"
            previous_model = previous.get("model_name") or "deepseek-v4-flash"
            show_page("配置 AI 做题", "第 1 步 / 3 · 接口地址", (f"当前地址：{previous_base}", "直接回车保留；Esc 取消返回。"))
            base = prompt_field("接口地址：", input_fn=field_reader).strip() or previous_base
            show_page("配置 AI 做题", "第 2 步 / 3 · 模型名称", (f"当前模型：{previous_model}", "直接回车保留；Esc 取消返回。"))
            model = prompt_field("模型名称：", input_fn=field_reader).strip() or previous_model
            show_page("配置 AI 做题", "第 3 步 / 3 · API 密钥",
                      ("密钥输入隐藏显示；不会显示已有密钥。", "接口地址未变时，直接回车保留已有密钥。", "Esc 取消，不保存本次更改。"))
            key = prompt_field("API 密钥：", hidden=True, input_fn=field_reader).strip()
            if not key and base.rstrip("/") == previous_base.rstrip("/"):
                key = previous.get("api_key") or ""
            save_settings(path, base, model, key)
            show_result_page("配置 AI 做题", "配置已保存", (f"接口：{base}", f"模型：{model}", "API 密钥已保存到本机，不在页面显示。"),
                             key_reader=key_reader)
        except (MenuBack, KeyboardInterrupt):
            return BACK_CODE
        except (OSError, ValueError, TypeError):
            show_result_page("配置 AI 做题", "配置未保存", ("请检查接口、模型、密钥及原配置文件格式。",), key_reader=key_reader)


def main():
    if "--check" in sys.argv:
        print("[OK] AI settings UI dependencies available; no config changes.")
        return 0
    if "--gui" not in sys.argv:
        return run_ai_settings_page()
    import tkinter as tk
    from tkinter import ttk
    root = tk.Tk()
    root.title("AI 做题 · 模型接口配置")
    root.geometry("650x330")
    root.resizable(False, False)
    frame = ttk.Frame(root, padding=24)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="DeepSeek API 配置", font=("Microsoft YaHei UI", 15, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 16))
    base = tk.StringVar(value="https://api.deepseek.com")
    model = tk.StringVar(value="deepseek-v4-flash")
    secret = tk.StringVar()
    for row, (label, variable) in enumerate((("接口地址", base), ("模型名称", model), ("API 密钥", secret)), 1):
        ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=7, padx=(0, 14))
        entry = ttk.Entry(frame, textvariable=variable, width=58, show="*" if row == 3 else "")
        entry.grid(row=row, column=1, sticky="ew", pady=7)
        if row == 3:
            entry.focus_set()
    ttk.Label(frame, text="密钥仅保存到本机 config.json，不在终端或聊天中显示。\n保存后使用独立模型服务；本窗口不会读取课程或提交答案。", foreground="#555555").grid(row=4, column=0, columnspan=2, sticky="w", pady=(12, 8))
    status = tk.StringVar()
    status_label = ttk.Label(frame, textvariable=status, wraplength=580)
    status_label.grid(row=5, column=0, columnspan=2, sticky="w")

    def save():
        try:
            save_settings(CONFIG_PATH, base.get(), model.get(), secret.get())
        except (OSError, ValueError, TypeError):
            status.set("保存失败：请检查地址、模型、密钥和原配置文件格式；未成功更新配置。")
            status_label.configure(foreground="#b42318")
        else:
            secret.set("")
            status.set("已保存。可以关闭窗口，返回对话继续验证 DeepSeek 与逐题保存。")
            status_label.configure(foreground="#067647")

    ttk.Button(frame, text="保存配置", command=save).grid(row=6, column=1, sticky="e", pady=(12, 0))
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
