"""比較 原檔／千問壓的／Claude 壓的 三份：token 數＋自動檢查有沒有掉東西。

自動檢查只抓得到「字面上」掉的：動作名（"t":"…"）、{變數}、【面板段落名】、使用者交代的日期。
規則的意思有沒有跑掉，要另外人工（或第三方）逐條核對。
用法：python 壓縮試驗/compare.py        → 寫 壓縮試驗/比較.md
"""
import glob
import json
import os
import re
import sys
import urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.path.join(HERE, "壓縮試驗")
EP = json.load(open(os.path.join(HERE, "設定.json"), encoding="utf-8"))["端點"].rstrip("/")
MODEL = json.loads(urllib.request.urlopen(EP + "/models", timeout=10).read())["data"][0]["id"]


def tok(s):
    if not s.strip():
        return 0
    req = urllib.request.Request(EP.replace("/v1", "") + "/tokenize",
                                 data=json.dumps({"model": MODEL, "prompt": s, "add_special_tokens": False}).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=60).read())["count"]


def body(p):
    if not os.path.exists(p):
        return None
    raw = open(p, encoding="utf-8").read()
    if raw.startswith("---"):
        raw = raw[3:].partition("---")[2]
    return raw.strip()


def keys(src):
    return {
        "動作": set(re.findall(r'"t"\s*:\s*"([^"]+)"', src)),
        "變數": set(re.findall(r"\{[a-z_]+\}", src)),
        "面板名": set(re.findall(r"【([^】]{2,14})】", src)),
        "日期": set(re.findall(r"(?:2026-)?(\d{2}[-/]\d{2})", src)),
    }


def lost(src, out):
    res = {}
    for kind, ks in keys(src).items():
        if kind == "面板名":
            miss = sorted(k for k in ks if k not in out)
        elif kind == "日期":
            o = out.replace("-", "/")
            miss = sorted(k for k in ks if k.replace("-", "/") not in o)
        else:
            miss = sorted(k for k in ks if k not in out)
        res[kind] = miss
    return res


def dup_rate(texts, n=12):
    """重複率：把字串去掉空白與標點後切成 n 字片段，算「出現兩次以上的片段」佔全部片段的比例。

    texts 傳一份文件＝量檔內重複；傳整個版本的所有文件＝連跨檔重複一起量。
    """
    from collections import Counter
    grams = Counter()
    for t in texts:
        s = re.sub(r"[\s\W_]+", "", t)
        for i in range(len(s) - n + 1):
            grams[s[i:i + n]] += 1
    total = sum(grams.values())
    return (sum(v for v in grams.values() if v > 1) / total) if total else 0.0


def main():
    rows, tot = [], {"原": 0, "千問": 0, "Claude": 0}
    allt = {"原": [], "千問": [], "Claude": []}
    lost_all = {"千問": [], "Claude": []}
    for d in ("共用", "基礎操作", "學習模組"):
        for p in sorted(glob.glob(os.path.join(HERE, "核心", d, "*.md"))):
            n = os.path.basename(p)
            q, c = body(os.path.join(BASE, "千問", d, n)), body(os.path.join(BASE, "Claude", d, n))
            if q is None or c is None:
                continue
            src = body(p)
            t0, tq, tc = tok(src), tok(q), tok(c)
            tot["原"] += t0; tot["千問"] += tq; tot["Claude"] += tc
            allt["原"].append(src); allt["千問"].append(q); allt["Claude"].append(c)
            lq, lc = lost(src, q), lost(src, c)
            rows.append((d, n, t0, tq, tc, lq, lc))
            for who, l in (("千問", lq), ("Claude", lc)):
                for kind, miss in l.items():
                    if miss:
                        lost_all[who].append(f"- `{n}` 掉了{kind}：" + "、".join(miss))
    pct = lambda a, b: f"{(a - b) * 100 // max(1, a)}%"
    out = ["# 三份比較：原檔／千問／Claude", "",
           f"tokenizer：{MODEL}（只算內文，不含檔頭）", "",
           f"| | 原檔 | 千問 | Claude |", "|---|---|---|---|",
           f"| 合計 tokens | {tot['原']} | {tot['千問']} | {tot['Claude']} |",
           f"| 省 | — | {pct(tot['原'], tot['千問'])} | {pct(tot['原'], tot['Claude'])} |",
           "| 重複率（整個版本，含跨檔） | " + " | ".join(f"{dup_rate(allt[k]) * 100:.1f}%" for k in ("原", "千問", "Claude")) + " |",
           "| 重複率（只算檔內，各檔平均） | " + " | ".join(
               f"{sum(dup_rate([t]) for t in allt[k]) * 100 / max(1, len(allt[k])):.1f}%" for k in ("原", "千問", "Claude")) + " |",
           "", "重複率＝去掉空白標點後，每 12 個字一段，出現兩次以上的片段佔全部的比例（字面重複；換句話說的重複抓不到）。", "",
           "| 資料夾 | 文件 | 原檔 | 千問 | 省 | Claude | 省 | 千問掉的（動作／變數） | Claude 掉的（動作／變數） |",
           "|---|---|---|---|---|---|---|---|---|"]
    for d, n, t0, tq, tc, lq, lc in rows:
        f = lambda l: "、".join(l["動作"] + l["變數"]) or "無"
        out.append(f"| {d} | {n} | {t0} | {tq} | {pct(t0, tq)} | {tc} | {pct(t0, tc)} | {f(lq)} | {f(lc)} |")
    for who in ("千問", "Claude"):
        out += ["", f"## {who} 版字面上掉的東西（動作名、變數、【面板名】、日期）", ""] + (lost_all[who] or ["沒有"])
    open(os.path.join(BASE, "比較.md"), "w", encoding="utf-8").write("\n".join(out) + "\n")
    sys.stdout.reconfigure(encoding="utf-8")
    print("\n".join(out[:10]))
    for who in ("千問", "Claude"):
        print(who, "字面掉的項目數：", {k: sum(len(r[5 if who == "千問" else 6][k]) for r in rows) for k in ("動作", "變數", "面板名", "日期")})


if __name__ == "__main__":
    main()
