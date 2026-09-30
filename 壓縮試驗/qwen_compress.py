"""讓 DGX 上的千問自己壓縮核心文件（2026-09-30 使用者要求的試驗）。

原檔不動；壓縮結果寫到 壓縮試驗/千問/<資料夾>/<檔名>，統計寫到 壓縮試驗/千問/結果.md。
代理不會載入這個資料夾（它只讀 核心/共用、基礎操作、學習模組 與 專案/<遊戲>/提示）。

用法：python 壓縮試驗/qwen_compress.py [檔名關鍵字…]   （不給就跑全部）
"""
import glob
import json
import os
import re
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(HERE, "壓縮試驗", "千問")
EP = json.load(open(os.path.join(HERE, "設定.json"), encoding="utf-8"))["端點"].rstrip("/")
MARK = "=====抵觸====="

ASK = """下面是你自己每一輪都會讀到的一份核心文件。請你自己把它壓縮。

要求：
1. 每一條規則、每一個動作的 JSON 格式（例如 {"t":"..."}）、每一個數字、每一個 {ppp} 這種大括號變數、每一個【】裡提到的面板段落名稱，都要留著，一個都不能少。
2. 同一件事講了好幾遍的，合併成一次。解釋「為什麼」的長段落縮成一句或拿掉。
3. 不要加原文沒有的規則，不要改規則的意思。
4. 如果原文有互相抵觸的地方，保留日期比較新的那一條（標了「使用者交代／使用者定」且日期較晚的優先）。
5. 保留【標題】這種分段寫法，用繁體中文。

輸出格式：先輸出壓縮後的文件全文；然後換行輸出一行 """ + MARK + """；
最後列出你發現的互相抵觸之處與你保留了哪一條（沒有就寫「沒有」）。不要輸出其他說明。

===== 原文 =====
"""


def post(path, body, timeout=1800):
    req = urllib.request.Request(EP.replace("/v1", "") + path if path == "/tokenize" else EP + path,
                                 data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


def tok(model, s):
    return post("/tokenize", {"model": model, "prompt": s, "add_special_tokens": False}, 60)["count"] if s.strip() else 0


def body_of(raw):
    head = ""
    if raw.startswith("---"):
        h, _, rest = raw[3:].partition("---")
        head, raw = "---" + h + "---\n\n", rest
    return head, raw.strip()


def main():
    model = json.loads(urllib.request.urlopen(EP + "/models", timeout=10).read())["data"][0]["id"]
    files = []
    for d in ("共用", "基礎操作", "學習模組"):
        for p in sorted(glob.glob(os.path.join(HERE, "核心", d, "*.md"))):
            n = os.path.basename(p)
            if not n.startswith(("README", "_", ".")):
                files.append((d, p))
    keys = sys.argv[1:]
    if keys:
        files = [f for f in files if any(k in f[1] for k in keys)]
    rows = []
    for d, p in files:
        name = os.path.basename(p)
        head, src = body_of(open(p, encoding="utf-8").read())
        meta = dict(l.split(":", 1) for l in head.strip("-\n ").split("\n") if ":" in l)
        if "全部" not in meta.get("適用", "全部") or meta.get("停用", "").strip() in ("是", "true", "1"):
            continue                                   # 這次只試每輪固定載入的 system 模組
        t0 = tok(model, src)
        if t0 < 120:
            print(f"{name}: {t0} tok，太短，跳過", flush=True)
            continue
        st = time.time()
        try:
            r = post("/chat/completions", {
                "model": model, "messages": [{"role": "user", "content": ASK + src}],
                "max_tokens": t0 + 600, "temperature": 0.2,
                "chat_template_kwargs": {"enable_thinking": False}})
        except Exception as e:  # noqa: BLE001
            print(f"{name}: 失敗 {e}", flush=True)
            rows.append((d, name, t0, None, 0, "請求失敗", ""))
            continue
        out = (r["choices"][0]["message"].get("content") or "").strip()
        fin = r["choices"][0].get("finish_reason")
        doc, _, conflict = out.partition(MARK)
        doc, conflict = doc.strip(), conflict.strip()
        t1 = tok(model, doc)
        # 自動檢查：動作名、大括號變數有沒有掉
        acts = set(re.findall(r'"t"\s*:\s*"([^"]+)"', src))
        vars_ = set(re.findall(r"\{[a-z_]+\}", src))
        lost = sorted(a for a in acts if a not in doc) + sorted(v for v in vars_ if v not in doc)
        os.makedirs(os.path.join(OUT, d), exist_ok=True)
        with open(os.path.join(OUT, d, name), "w", encoding="utf-8") as fh:
            fh.write(head + doc + "\n")
        note = ("被截斷" if fin == "length" else "") + ("｜沒照格式分段" if MARK not in out else "")
        rows.append((d, name, t0, t1, time.time() - st, note, "、".join(lost), conflict))
        print(f"{name}: {t0} → {t1} tok（省 {(t0 - t1) * 100 // max(1, t0)}%）{time.time() - st:.0f}s "
              f"{note} 掉了：{'、'.join(lost) or '無'}", flush=True)
    ok = [r for r in rows if r[3] is not None]
    a, b = sum(r[2] for r in ok), sum(r[3] for r in ok)
    with open(os.path.join(OUT, "結果.md"), "w", encoding="utf-8") as fh:
        fh.write(f"# 千問自己壓縮核心文件（{time.strftime('%Y-%m-%d %H:%M')}，模型 {model}，不開推理）\n\n")
        fh.write(f"合計 {a} → {b} tokens（省 {a - b}，{(a - b) * 100 // max(1, a)}%）\n\n")
        fh.write("| 資料夾 | 文件 | 原本 | 壓縮後 | 省 | 秒 | 備註 | 掉了的動作／變數 |\n|---|---|---|---|---|---|---|---|\n")
        for r in rows:
            if r[3] is None:
                fh.write(f"| {r[0]} | {r[1]} | {r[2]} | — | — | — | {r[5]} | |\n")
            else:
                fh.write(f"| {r[0]} | {r[1]} | {r[2]} | {r[3]} | {(r[2] - r[3]) * 100 // max(1, r[2])}% "
                         f"| {r[4]:.0f} | {r[5]} | {r[6]} |\n")
        fh.write("\n## 他自己列的抵觸\n")
        for r in ok:
            if r[7] and r[7] != "沒有":
                fh.write(f"\n### {r[1]}\n{r[7]}\n")
    print(f"合計 {a} → {b}（省 {(a - b) * 100 // max(1, a)}%）", flush=True)


if __name__ == "__main__":
    main()
