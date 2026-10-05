# 深度研究 2：面板存檔逐日統計——E 提示出現多少、出現時他做了什麼、按 E 時有沒有提示、他把什麼當目標（只讀）
import gzip, json, sys, io, os, re, glob, collections, statistics
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = r"D:\Documents\ThorWorkSpace\gamer AI\專案"
GAMES = ["動物醫院", "農場遊戲"]
only = sys.argv[1:]  # 可指定 日期 清單


def sec(u, head):
    i = u.find(head)
    if i < 0:
        return None
    j = u.find("\n【", i + 1)
    return u[i:(j if j > 0 else len(u))]


def acts_of(out):
    try:
        o = json.loads(out)
    except Exception:
        m = re.search(r"\{.*\}", out or "", re.S)
        try:
            o = json.loads(m.group(0)) if m else {}
        except Exception:
            o = {}
    return o if isinstance(o, dict) else {}


def cls(a):
    t = a.get("t", "")
    k = str(a.get("k", "")).lower()
    if t in ("hold",) and k == "e" or (t == "key" and k == "e"):
        return "按E"
    if t in ("click", "點字", "點文字", "點"):
        return "點擊"
    if t == "key" and k in ("w", "a", "s", "d"):
        return "走"
    if t in ("走到", "走到記過的", "走向"):
        return "走"
    if t in ("look", "aim", "水平歸零"):
        return "轉頭"
    if t == "key":
        return "其他鍵"
    if t in ("wait", "等"):
        return "等"
    return "工具"


for g in GAMES:
    for p in sorted(glob.glob(os.path.join(ROOT, g, "記憶", "面板", "*.jsonl.gz"))):
        day = os.path.basename(p)[5:10]
        if only and day not in only:
            continue
        C = collections.Counter()
        first_when_prompt = collections.Counter()
        first_all = collections.Counter()
        tasks = collections.Counter(); bigs = collections.Counter(); flows = collections.Counter()
        tgt = collections.Counter()
        ulen = []; olen = []
        prev = None     # 上一筆 (mode, prompt_state)
        with gzip.open(p, "rt", encoding="utf-8", errors="ignore") as f:
            for line in f:
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                u = o.get("user", "") or ""
                mode = o.get("模式", "?")
                C["列"] += 1; C["模式:" + str(mode)] += 1
                seen = str(o.get("反射看到", ""))
                if re.search(r"person（中[中下]）", seen): C["眼睛框到正中央的person"] += 1
                if "person" in seen: C["眼睛框到person"] += 1
                # 這一筆的【上一步】＝上一筆實際送出的動作與結果
                last = sec(u, "【上一步】") or ""
                sent = re.search(r"你送出：([^\n]*)", last)
                sent = sent.group(1) if sent else ""
                if ("長按 E" in sent) or re.search(r"按 e \d", sent):
                    C["實際按E"] += 1
                    if "成功" in last: C["實際按E成功"] += 1
                    if prev:
                        C[f"按E時·{prev[0]}·{prev[1]}"] += 1
                ep = sec(u, "【畫面上有沒有 E 提示】")
                st = "無此段"
                if ep:
                    st = "有提示" if "✅" in ep else ("沒提示" if "❌" in ep else "不明")
                if mode in ("快思", "中想"):
                    C["快思列"] += 1
                    C["快思·" + st] += 1
                    ulen.append(len(u)); olen.append(len(str(o.get("輸出", ""))))
                    d = acts_of(str(o.get("輸出", "")))
                    a = d.get("a") or []
                    a = [x for x in a if isinstance(x, dict)]
                    kinds = [cls(x) for x in a]
                    press = [k for k in kinds if k not in ("工具", "等")]
                    fk = press[0] if press else ("只有工具或等" if kinds else "沒動作")
                    first_all[fk] += 1
                    if st == "有提示":
                        first_when_prompt[fk] += 1
                        if "按E" in kinds: C["有提示且這輪a裡有E"] += 1
                        steps = d.get("步") or []
                        anyE = any(cls(x) == "按E" for s in steps if isinstance(s, dict) for x in (s.get("a") or []) if isinstance(x, dict))
                        if anyE or "按E" in kinds: C["有提示且三步內有E"] += 1
                    think = str(d.get("想", "")) + str(d.get("s", ""))
                    if re.search(r"人偶|背影", think): C["快思想到人偶背影"] += 1
                    if re.search(r"自己的(角色|背影|人物)|我自己|我的角色", think): C["快思知道是自己"] += 1
                    if re.search(r"無E|沒有E|沒E|無 E|沒有 E", think): C["快思寫無E提示"] += 1
                    for x in a:
                        gname = str(x.get("g", ""))[:8]
                        if gname and cls(x) in ("走", "按E", "轉頭"):
                            tgt[gname] += 1
                    tasks[str(o.get("任務", ""))[:14]] += 1
                    flows[str(o.get("流程", ""))[:10]] += 1
                    bm = re.search(r"現在這個大動作：\*\*([^*]+)\*\*", u)
                    if bm: bigs[bm.group(1)[:14]] += 1
                prev = (("快思" if mode in ("快思", "中想") else str(mode)), st)
        n = C["列"]; q = max(C["快思列"], 1)
        print(f"===== {g} {day}：{n} 列｜" + "、".join(f"{k[3:]} {v}" for k, v in C.items() if k.startswith("模式:")))
        print(f"  快思 {C['快思列']} 輪：面板說 有E提示 {C['快思·有提示']}（{100*C['快思·有提示']/q:.1f}%）｜沒提示 {C['快思·沒提示']}｜無此段 {C['快思·無此段']}")
        print(f"  有提示的那些輪：這輪就按E {C['有提示且這輪a裡有E']}｜三步內排了E {C['有提示且三步內有E']}｜第一個按鍵動作：" + "、".join(f"{k}×{v}" for k, v in first_when_prompt.most_common()))
        print(f"  全部快思輪第一個按鍵動作：" + "、".join(f"{k} {100*v/q:.0f}%" for k, v in first_all.most_common()))
        print(f"  實際按E {C['實際按E']} 次，成功 {C['實際按E成功']}｜按下去那一輪的決定來源：" + "、".join(f"{k[5:]}×{v}" for k, v in C.items() if k.startswith("按E時·")))
        print(f"  眼睛框到 person {100*C['眼睛框到person']/max(n,1):.0f}% 的列，其中正中央（中中／中下）{100*C['眼睛框到正中央的person']/max(n,1):.0f}%｜快思的想法提到人偶／背影 {100*C['快思想到人偶背影']/q:.0f}%｜知道是自己 {100*C['快思知道是自己']/q:.0f}%｜寫「無E提示」{100*C['快思寫無E提示']/q:.0f}%")
        if ulen:
            print(f"  快思面板長度中位 {statistics.median(ulen):.0f} 字｜輸出中位 {statistics.median(olen):.0f} 字")
        print("  流程：" + "、".join(f"{k}×{v}" for k, v in flows.most_common(5)))
        print("  右上角任務欄位：" + "、".join(f"{k}×{v}" for k, v in tasks.most_common(6)))
        print("  大動作：" + "、".join(f"{k}×{v}" for k, v in bigs.most_common(8)))
        print("  動作的目標 g：" + "、".join(f"{k}×{v}" for k, v in tgt.most_common(14)))
        sys.stdout.flush()
