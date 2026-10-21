# 找長相實驗：參考圖張數／角度／框的來源／提示詞 → 準確率
import sys, os, json, time, re, random, glob, threading
from concurrent.futures import ThreadPoolExecutor
W = r"D:/Documents/ThorWorkSpace/gamer AI 工具版"
sys.path.insert(0, W + "/工具版"); sys.path.insert(0, W); os.chdir(W)
import numpy as np
import tool_agent as T
from play_agent import stream_json
from PIL import Image, ImageDraw
OUT = os.path.dirname(os.path.abspath(__file__)) + "/"
EP = "http://192.168.0.126:8888/v1"
MODEL = T.detect_model(EP)
LOCK = threading.Lock()
L = {}
for f in sorted(glob.glob(OUT + "labels_*.json")):
    L.update(json.load(open(f, encoding="utf-8")))
if len(sys.argv) > 2:
    L = {k: v for k, v in L.items() if k in sys.argv[2].split(",")}
_img = {}


def img(obj, name):
    p = L[obj]["dir"] + name + ("" if name.endswith(".jpg") else "_定時.jpg")
    if p not in _img:
        im = Image.open(p).convert("RGB")
        _img[p] = im.resize((1024, int(1024 * im.height / im.width)))
    return _img[p]


def gray(im):
    return np.asarray(im.convert("L").resize((64, 36)), dtype=float)


def marked(im, box):
    m = im.copy()
    W_, H_ = m.size
    ImageDraw.Draw(m).rectangle((int(box[0] * W_), int(box[1] * H_), int(box[2] * W_), int(box[3] * H_)), outline=(255, 0, 0), width=4)
    return m


def ask(system, content, effort="快思", max_tokens=40, limit=15):
    t0 = time.time()
    try:
        raw, *_ = stream_json(EP, MODEL, system, content, max_tokens, time.time() + limit, 0.2, effort=effort)
    except Exception as e:  # noqa: BLE001
        raw = f"ERR {e}"
    return str(raw or ""), time.time() - t0


def nums(s):
    return [float(n) for n in re.findall(r"[01]?\.\d+|[01](?![\d.])", s)]


def iou(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0])); iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - ix * iy
    return ix * iy / u if u > 0 else 0


# ---------- 1. 他自己框（不思考／中想）
BOXQ = "把畫面上的{d}整個框起來（有好幾個就選最清楚的一個）。只回一行：x1,y1,x2,y2（0～1，左上到右下）；畫面上沒有就只回：沒有"
BOXES = json.load(open(OUT + "boxes.json", encoding="utf-8")) if os.path.exists(OUT + "boxes.json") else {}


def get_box(obj, name, src):
    k = f"{obj}|{name}|{src}"
    if k in BOXES:
        return BOXES[k]
    d = L[obj]["desc"]
    c = [{"type": "image_url", "image_url": {"url": T.img_uri(img(obj, name))}}]
    if src == "快框":
        raw, dt = ask("你是畫面標記員。不要思考，直接回答。", c + [{"type": "text", "text": BOXQ.format(d=d)}])
    else:
        raw, dt = ask("你是畫面標記員。先想清楚畫面上哪個是它、它的邊界在哪，再回答。",
                      c + [{"type": "text", "text": BOXQ.format(d=d) + "（想完之後，最後一行只寫 x1,y1,x2,y2 或 沒有）"}], effort="中想", max_tokens=4000, limit=120)
    last = [ln for ln in raw.strip().splitlines() if ln.strip()][-1:] or [""]
    v = nums(last[0])[:4]
    box = v if len(v) == 4 and v[0] < v[2] and v[1] < v[3] else None
    gold = dict((i[0], i[1]) for i in L[obj]["items"]).get(name)
    r = {"box": box, "iou": round(iou(box, gold), 3) if box and gold else 0, "秒": round(dt, 1), "raw": raw[-120:]}
    with LOCK:
        BOXES[k] = r
        json.dump(BOXES, open(OUT + "boxes.json", "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    return r


# ---------- 2. 他自己寫的找東西提示詞
PROMPTS = json.load(open(OUT + "prompts.json", encoding="utf-8")) if os.path.exists(OUT + "prompts.json") else {}


def own_prompt(obj, refs):
    if obj in PROMPTS:
        return PROMPTS[obj]
    c = []
    for i, (n, b) in enumerate(refs[:2]):
        c += [{"type": "text", "text": f"【以前的畫面 {i + 1}，紅框框起來的是要找的東西】"}, {"type": "image_url", "image_url": {"url": T.img_uri(marked(img(obj, n), b))}}]
    c += [{"type": "text", "text": "你等一下要叫一個「不思考的眼睛」幫你找紅框裡的這個東西。它會看到幾張以前的畫面（紅框框起來的是要找的東西）和一張現在的畫面，"
                                   "要回答現在的畫面上有沒有這個東西、在哪裡。請你寫給它的提示詞：告訴它要找什麼、怎麼認、要小心別認錯成什麼。只輸出提示詞本身（一段話，200 字內）。"}]
    raw, dt = ask("你是遊戲代理的大腦。", c, effort="中想", max_tokens=4000, limit=150)
    txt = raw.strip().split("</think>")[-1].strip()[-600:]
    with LOCK:
        PROMPTS[obj] = txt
        json.dump(PROMPTS, open(OUT + "prompts.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return txt


# ---------- 3. 找
FIXED = "現在的畫面上有沒有紅框裡的那個東西（同一個東西，角度、遠近可能不同；現在的畫面沒有紅框）？"
FMT = "有就只回一行：有 (x,y)，x,y 是它中心在現在畫面上的位置 0～1；沒有就只回：沒有"


def find(obj, refs, test, prompt):
    c = []
    for i, (n, b) in enumerate(refs):
        c += [{"type": "text", "text": f"【以前的畫面 {i + 1}，紅框框起來的是要找的東西】"}, {"type": "image_url", "image_url": {"url": T.img_uri(marked(img(obj, n), b))}}]
    if not refs:                                         # 0 張：只給名字
        prompt = f"現在的畫面上有沒有「{L[obj]['desc']}」？"
    c += [{"type": "text", "text": "【現在的畫面】"}, {"type": "image_url", "image_url": {"url": T.img_uri(img(obj, test))}}, {"type": "text", "text": prompt + FMT}]
    raw, dt = ask("你是找東西的眼睛。前面是以前的畫面（紅框是要找的東西），最後一張是現在的遊戲畫面。不要思考，直接回答。" if refs
                  else "你是找東西的眼睛。不要思考，直接回答。", c)
    m = re.search(r"有\s*[(（]?\s*([01]?\.\d+|[01])\s*[,，]\s*([01]?\.\d+|[01])", raw)
    return (float(m.group(1)), float(m.group(2))) if m else None, raw, dt


def score(obj, test, xy):
    gold = dict((i[0], i[1]) for i in L[obj]["items"])[test]
    if gold is None:
        return xy is None
    if xy is None:
        return False
    g = 0.05
    return gold[0] - g <= xy[0] <= gold[2] + g and gold[1] - g <= xy[1] <= gold[3] + g


RES = OUT + "results.jsonl"
done = set()
if os.path.exists(RES):
    for ln in open(RES, encoding="utf-8", errors="replace"):
        try:
            done.add(json.loads(ln)["id"])
        except ValueError:                               # 兩個程序同時寫，偶爾黏在一起
            pass


def trial(obj, K, mode, src, pr, seed):
    items = L[obj]["items"]
    pos = [i for i in items if i[1]]
    neg = [i[0] for i in items if not i[1]]
    rnd = random.Random(f"{obj}{K}{mode}{seed}")
    cand = pos[:]
    if src != "金框":
        cand = [i for i in cand if get_box(obj, i[0], src)["box"]]
    if mode == "同角度":
        groups = {}
        for i in cand:
            groups.setdefault(i[2], []).append(i)
        ok = [g for a, g in groups.items() if a and len(g) >= K + 2]
        if not ok:
            return []
        g = rnd.choice(sorted(ok, key=lambda g: g[0][2]))
        refs = rnd.sample(g, K)
    else:
        rnd.shuffle(cand)
        refs, seen = [], set()
        for i in cand:                                   # 多角度：先每個角度挑一張，再補
            if i[2] not in seen:
                refs.append(i); seen.add(i[2])
        refs = (refs + [i for i in cand if i not in refs])[:K]
    if len(refs) < K:
        return []
    rg = [gray(img(obj, r[0])) for r in refs]
    tests = []
    for i in pos:
        if i in refs:
            continue
        gi = gray(img(obj, i[0]))
        if all(np.abs(gi - r).mean() > 8 for r in rg):  # 考題不能跟手上的圖幾乎一樣
            tests.append(i[0])
    tests += neg
    rb = [(r[0], r[1] if src == "金框" else get_box(obj, r[0], src)["box"]) for r in refs]
    if pr == "固定":
        prompt = FIXED
    elif pr == "名字":                                    # 參考圖＋名字
        prompt = f"要找的是「{L[obj]['desc']}」。" + FIXED
    else:
        prompt = own_prompt(obj, [(i[0], i[1]) for i in pos[:2]]) + "\n"
    angles = dict((i[0], i[2]) for i in items)
    jobs = [(f"{obj}|K{K}|{mode}|{src}|{pr}|s{seed}|{t}", t) for t in tests]
    jobs = [j for j in jobs if j[0] not in done]

    def one(j):
        rid, t = j
        xy, raw, dt = find(obj, rb, t, prompt)
        r = {"id": rid, "obj": obj, "K": K, "mode": mode, "src": src, "pr": pr, "seed": seed, "test": t, "refs": [r[0] for r in refs],
             "ref_angles": [r[2] for r in refs], "test_angle": angles[t], "pos": t not in neg,
             "xy": xy, "ok": score(obj, t, xy), "raw": raw[:60], "秒": round(dt, 2)}
        with LOCK:
            open(RES, "a", encoding="utf-8").write(json.dumps(r, ensure_ascii=False) + "\n")
        return r
    with ThreadPoolExecutor(4) as ex:
        return list(ex.map(one, jobs))


phase = sys.argv[1]
for obj in L:
    angled = any(i[2] for i in L[obj]["items"])
    if phase == "boxes":
        with ThreadPoolExecutor(4) as ex:
            list(ex.map(lambda a: get_box(obj, a[0], a[1]), [(i[0], s) for i in L[obj]["items"] for s in ("快框", "想框")]))
        print(obj, "boxes", flush=True)
    if phase == "main":
        for seed in range(3):
            for K in (7, 4, 2, 1, 0):
                for mode in (("多角度", "同角度") if angled and K else ("多角度",)):
                    if K == 0 and seed > 0:
                        continue
                    trial(obj, K, mode, "金框", "固定", seed)
        print(obj, "main", flush=True)
    if phase == "src":
        for seed in range(2):
            for K in (2, 4):
                for src in ("金框", "快框", "想框"):
                    for pr in ("固定", "自己"):
                        trial(obj, K, "多角度", src, pr, seed)
        print(obj, "src", flush=True)
    if phase == "name":
        for seed in range(2):
            for K in (1, 2, 4):
                trial(obj, K, "多角度", "金框", "名字", seed)
                trial(obj, K, "多角度", "快框", "名字", seed)
        print(obj, "name", flush=True)
if phase == "smoke":
    o = next(iter(L))
    for t in ["121231", "021957"]:
        print(find(o, [("151027", (0.20, 0.25, 0.38, 0.42)), ("000024", (0.17, 0.26, 0.27, 0.37))], t, FIXED))
    print(get_box(o, "104339", "快框"))
