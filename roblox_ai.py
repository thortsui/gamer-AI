#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gamer AI — Roblox 教練大腦（命令列聊天客戶端）

連到 DGX 上的 Qwen3.8-Flash-Next（vLLM，OpenAI 相容 API），
把「知識庫/Roblox玩家入門說明書.md」整份塞進 system prompt，
讓模型當「教你怎麼玩 Roblox」的教練。

用法：
    python roblox_ai.py                 # 互動聊天
    python roblox_ai.py "怎麼裝 Roblox"  # 單問單答後結束
    python roblox_ai.py --check         # 只檢查 DGX 服務活著沒

聊天中的指令：
    /img <圖片路徑>   把一張截圖跟下一句話一起送（模型看得懂圖）
    /think            切換「思考模式」開／關（預設關，關比較快）
    /clear            清掉對話歷史
    /quit             離開

不需要安裝任何套件，只用 Python 標準函式庫。
"""
import base64
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.request

# Windows 終端機預設 cp950，印 emoji 會炸；一律改成 UTF-8（印不出的字用 ? 代替）
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "設定.json")
DEFAULT_CONFIG = {
    "端點": "http://192.168.0.126:8888/v1",
    "備援端點": "http://192.168.0.126:8101/v1",
    "模型名": "",
    "知識庫": "知識庫/Roblox玩家入門說明書.md",
    "溫度": 0.4,
    "max_tokens": 1200,
    "對話帶入則數": 20,
    "思考模式": False,
    "逾時秒": 180,
}

SYSTEM_PREFIX = """你是「gamer AI」，一位專門教小朋友和新手怎麼玩 Roblox 的教練。
你的知識來自下面這份《Roblox 玩家入門說明書》。回答時：
- 用繁體中文、台灣用語，語氣親切、句子短。
- 一次只講下一步，必要時再問玩家「你現在卡在哪」。
- 說明書沒寫的遊戲細節，就引導玩家去看遊戲頁面的說明或畫面提示，不要編造按鍵。
- 有人問免費 Robux、外掛、腳本，要明確說那是詐騙或會被封號。
- 若玩家附上截圖，先描述你在圖裡看到什麼，再給建議。

===== Roblox 玩家入門說明書 開始 =====
"""
SYSTEM_SUFFIX = "\n===== Roblox 玩家入門說明書 結束 =====\n"


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg.update(json.load(f))
    else:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    return cfg


def http_json(url, payload=None, timeout=30):
    data = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def detect_model(endpoint, timeout=10):
    """打 /v1/models 取第一個 id；不寫死模型名，換模型也不會 404。"""
    info = http_json(endpoint.rstrip("/") + "/models", timeout=timeout)
    models = info.get("data") or []
    if not models:
        raise RuntimeError("端點回應了，但沒有任何模型")
    return models[0]["id"]


def pick_endpoint(cfg):
    for key in ("端點", "備援端點"):
        ep = cfg.get(key)
        if not ep:
            continue
        try:
            model = cfg.get("模型名") or detect_model(ep)
            return ep, model, key
        except Exception as e:  # noqa: BLE001
            print(f"[warn] {key} {ep} 連不上：{e}")
    raise SystemExit("兩個端點都連不上。DGX 的模型是不是還沒啟動？（見 DGX連線說明.md）")


def load_knowledge(cfg):
    path = os.path.join(HERE, cfg["知識庫"])
    with open(path, encoding="utf-8") as f:
        return f.read()


def image_part(path):
    mime = mimetypes.guess_type(path)[0] or "image/png"
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}


def chat(cfg, endpoint, model, system_text, history, user_text, image_path=None, thinking=False):
    content = [{"type": "text", "text": user_text}]
    if image_path:
        content.insert(0, image_part(image_path))
    messages = [{"role": "system", "content": system_text}]
    messages += history[-cfg["對話帶入則數"]:]
    messages.append({"role": "user", "content": content})

    payload = {
        "model": model,
        "messages": messages,
        "temperature": cfg["溫度"],
        "max_tokens": cfg["max_tokens"],
        "chat_template_kwargs": {"enable_thinking": bool(thinking)},
    }
    t0 = time.time()
    resp = http_json(endpoint.rstrip("/") + "/chat/completions", payload, timeout=cfg["逾時秒"])
    dt = time.time() - t0
    msg = resp["choices"][0]["message"]
    usage = resp.get("usage", {})
    reply = (msg.get("content") or "").strip()
    reasoning = (msg.get("reasoning") or msg.get("reasoning_content") or "").strip()
    if not reply and reasoning:
        reply = "（模型只輸出了思考、沒有答案；請關掉思考模式或提高 max_tokens）"
    tok = usage.get("completion_tokens", 0)
    stats = f"{dt:.1f}s, {tok} tok, {tok/dt if dt else 0:.1f} tok/s"
    return reply, reasoning, stats


def main():
    cfg = load_config()
    args = sys.argv[1:]

    if args and args[0] == "--check":
        for key in ("端點", "備援端點"):
            ep = cfg.get(key)
            try:
                print(f"{key} {ep} -> {detect_model(ep)}")
            except Exception as e:  # noqa: BLE001
                print(f"{key} {ep} -> 連不上 ({e})")
        return

    endpoint, model, which = pick_endpoint(cfg)
    knowledge = load_knowledge(cfg)
    system_text = SYSTEM_PREFIX + knowledge + SYSTEM_SUFFIX
    print(f"[gamer AI] 使用 {which} {endpoint}  模型={model}  知識庫={cfg['知識庫']} ({len(knowledge)} 字)")

    history = []
    thinking = bool(cfg.get("思考模式"))

    if args:  # 單問單答
        reply, _, stats = chat(cfg, endpoint, model, system_text, history, " ".join(args), thinking=thinking)
        print(reply)
        print(f"[{stats}]")
        return

    print("輸入問題開始聊（/img 圖片路徑、/think、/clear、/quit）")
    pending_image = None
    while True:
        try:
            line = input("\n你> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line in ("/quit", "/exit"):
            break
        if line == "/clear":
            history.clear()
            print("[已清空對話]")
            continue
        if line == "/think":
            thinking = not thinking
            print(f"[思考模式 {'開' if thinking else '關'}]")
            continue
        if line.startswith("/img "):
            p = line[5:].strip().strip('"')
            if os.path.exists(p):
                pending_image = p
                print(f"[下一句會附上圖片 {p}]")
            else:
                print("[找不到檔案]")
            continue

        try:
            reply, reasoning, stats = chat(cfg, endpoint, model, system_text, history, line,
                                           image_path=pending_image, thinking=thinking)
        except urllib.error.HTTPError as e:
            print(f"[HTTP {e.code}] {e.read().decode('utf-8', 'replace')[:500]}")
            continue
        except Exception as e:  # noqa: BLE001
            print(f"[錯誤] {e}")
            continue
        pending_image = None
        if thinking and reasoning:
            print(f"（思考：{reasoning[:300]}…）")
        print(f"\nAI> {reply}")
        print(f"[{stats}]")
        history.append({"role": "user", "content": line})
        history.append({"role": "assistant", "content": reply})


if __name__ == "__main__":
    main()
