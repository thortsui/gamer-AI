"""10/24 使用者：「挑戰完成後 派 sub Agent 用 haiku 玩一小時、再 sonnet 一小時、再 sonnet 開自由搜尋一小時，
能用的工具、能取得的資訊都跟本地 AI 一模一樣，記錄一樣的數據；看模型本身的智力、對圖片的判斷程度和速度
會不會影響任務成功率和產出」。

做法：把 play_agent.stream_json 換成這裡的 stream_json——每次叫大腦就開一個全新的 headless Claude（`claude -p`），
送的 system、面板文字、截圖跟本地模型那次一字不差（OpenAI image_url → Anthropic image block），回傳同樣的
(raw, 截斷, 首字秒數, usage)，後面的解析、執行、紀錄全部照舊。每次都是新的 session，跟本地模型一樣不帶上一輪的對話。
- 思考強度照 --effort 對應：快思推理 low、中想 medium、慢想 xhigh（跟本地 THINK_MODES 同一檔）。
- max_tokens 用 CLAUDE_CODE_MAX_OUTPUT_TOKENS 給；時限＝deadline 到了就殺掉當沒回（跟本地一樣算頂時限）。
- 搜尋版：多給 WebSearch／WebFetch 兩個工具（自動允許），其他一樣。
- 原生版（10/24 04:1x 使用者「第三次就使用原生的Claude 不要洗掉 試試看」）：不換掉 Claude Code 自己的 system，遊戲的 system
  接在後面（--append-system-prompt-file）；整段同一個 session（--resume，前面每一輪都記得，不洗掉）；工具用它原生那一套，
  自動允許 WebSearch／WebFetch／Read／Glob／Grep，其他（改檔、跑指令）在 -p 沒人批准會被擋。
- 每次呼叫另記一行到 記憶/工具版/claude大腦.jsonl：模型、秒數、API 秒數、token、費用、搜尋次數與搜尋字。
"""
import base64
import json
import re
import os
import subprocess
import tempfile
import time

EFFORT = {"反射": "low", "快思": "low", "快思推理": "low", "中想": "medium", "慢想": "xhigh"}
CFG = {"model": "", "search": False, "log": "", "label": "", "native": False, "session": "", "cost_total": 0.0}
CWD = tempfile.mkdtemp(prefix="claude_brain_")          # 空資料夾：不讀任何 CLAUDE.md


def setup(model, search, log_path, label, native=False):
    CFG.update(model=model, search=bool(search), log=log_path, label=label, native=bool(native))
    import play_agent
    play_agent.stream_json = stream_json


def _blocks(content):
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    cap = int(os.environ.get("GAMER_MAX_IMAGES", "8") or 8)        # 跟本地一樣：超過 8 張從最舊的丟
    idx = [i for i, c in enumerate(content) if isinstance(c, dict) and c.get("type") == "image_url"]
    drop = set(idx[:max(0, len(idx) - cap)])
    out = []
    for i, c in enumerate(content):
        if i in drop or not isinstance(c, dict):
            continue
        if c.get("type") == "text":
            if c.get("text"):
                out.append({"type": "text", "text": c["text"]})
        elif c.get("type") == "image_url":
            url = c["image_url"]["url"]
            head, b64 = url.split(",", 1)
            mt = head[5:].split(";")[0] or "image/jpeg"
            out.append({"type": "image", "source": {"type": "base64", "media_type": mt, "data": b64}})
    return out


def stream_json(endpoint, model, system_text, content, max_tokens, deadline_at,
                temperature=0.2, effort="快思", _retry=True):
    from play_agent import LAST_REASONING
    remaining = deadline_at - time.time()
    if remaining <= 0.2:
        return "", True, None, None
    sp = os.path.join(CWD, f"system_{os.getpid()}.txt")     # 系統提示可能超過 Windows 命令列 32K 字 → 用檔案給
    with open(sp, "w", encoding="utf-8") as f:
        f.write(system_text)
    cmd = ["claude", "-p", "--model", CFG["model"], "--input-format", "stream-json", "--output-format", "stream-json",
           "--verbose", "--setting-sources", "", "--strict-mcp-config", "--effort", EFFORT.get(effort, "medium")]
    if CFG["native"]:
        cmd += ["--append-system-prompt-file", sp, "--allowedTools", "WebSearch", "WebFetch", "Read", "Glob", "Grep",
                "--max-turns", "12"] + (["--resume", CFG["session"]] if CFG["session"] and not os.environ.get("CLAUDE_BRAIN_FRESH") else [])   # 10/24 研究：CLAUDE_BRAIN_FRESH＝原生 system 但每次新 session
    else:
        cmd += ["--system-prompt-file", sp, "--no-session-persistence"]
    if CFG["native"]:
        pass
    elif CFG["search"]:
        cmd += ["--tools", "WebSearch,WebFetch", "--allowedTools", "WebSearch", "WebFetch", "--max-turns", "12"]
    else:
        cmd += ["--tools", "", "--max-turns", "1"]
    env = dict(os.environ, CLAUDE_CODE_MAX_OUTPUT_TOKENS=str(int(max_tokens)))
    msg = {"type": "user", "message": {"role": "user", "content": _blocks(content)}}
    t0 = time.time()
    rec = {"時間": time.strftime("%H:%M:%S"), "實驗": CFG["label"], "模型": CFG["model"], "思考": EFFORT.get(effort, "medium"),
           "session": CFG["session"]}
    raw, trunc, ttft, usage, thinking, searches = "", False, None, None, [], []
    texts = []
    try:
        p = subprocess.run(cmd, input=json.dumps(msg, ensure_ascii=False) + "\n", capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=remaining, cwd=CWD, env=env)
        for line in p.stdout.splitlines():
            try:
                o = json.loads(line)
            except ValueError:
                continue
            if o.get("type") == "assistant":
                for b in (o.get("message") or {}).get("content") or []:
                    if b.get("type") == "thinking" and b.get("thinking"):
                        thinking.append(b["thinking"])
                    elif b.get("type") in ("tool_use", "server_tool_use"):
                        searches.append(f"{b.get('name')}:{json.dumps(b.get('input'), ensure_ascii=False)[:200]}")
                    elif b.get("type") == "text":
                        texts.append(b.get("text") or "")      # 10/24：result 只給最後一段文字（Haiku 4 輪 JSON 前半掉了）→ 全部接起來
                        if ttft is None:
                            ttft = time.time() - t0
            elif o.get("type") == "result":
                if CFG["native"] and o.get("session_id"):
                    CFG["session"] = o["session_id"]
                raw = o.get("result") or ""
                usage = o.get("usage")
                trunc = o.get("subtype") not in ("success", None) or (o.get("stop_reason") == "max_tokens")
                cost = o.get("total_cost_usd") or 0
                if CFG["native"]:                      # 同一個 session 回的是累計費用 → 記這次增加的
                    cost, CFG["cost_total"] = max(0.0, cost - CFG["cost_total"]), cost
                rec.update(API秒=round((o.get("duration_api_ms") or 0) / 1000, 1), 費用=round(cost, 6),
                           結果=o.get("subtype"), 輪數=o.get("num_turns"))
        if CFG["native"] and texts:
            raw = texts[-1] if (texts[-1] or "").lstrip().startswith(("{", "`")) else "".join(texts)   # 原生會先講話再呼叫工具，取最後的 JSON
        elif texts:
            # 10/24 19:4x：只接起來會出事（Haiku 先寫半段放棄、再寫一段完整的 → 接起來變壞）→ 依序試：result、每一段、全部接起來，取第一個能解析成物件的
            def _ok(t):
                t = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", t or "").strip()
                try:
                    return isinstance(json.loads(t), dict)
                except ValueError:
                    return False
            raw = next((c for c in [raw] + texts[::-1] + ["".join(texts)] if _ok(c)), raw if len(raw) >= len("".join(texts)) else "".join(texts))
        if not raw and p.returncode:
            rec["錯誤"] = (p.stderr or p.stdout)[-400:]
    except subprocess.TimeoutExpired:
        trunc, rec["錯誤"] = True, "頂時限"
    rec["秒"] = round(time.time() - t0, 1)
    if usage:
        rec.update(輸入=usage.get("input_tokens"), 快取讀=usage.get("cache_read_input_tokens"),
                   快取寫=usage.get("cache_creation_input_tokens"), 輸出=usage.get("output_tokens"),
                   思考字=(usage.get("output_tokens_details") or {}).get("thinking_tokens"),
                   搜尋次=(usage.get("server_tool_use") or {}).get("web_search_requests"))
    rec["工具"] = searches
    rec["回答字數"] = len(raw)
    LAST_REASONING["text"] = "\n".join(thinking)
    if CFG["log"]:
        with open(CFG["log"], "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return raw, trunc, ttft, usage
