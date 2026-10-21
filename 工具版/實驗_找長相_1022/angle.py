# 正視測試：紅框裡的東西，他看得出是正面／側面／背面嗎？在視線正前方還是偏左偏右？
import sys, os, json, re, glob
from concurrent.futures import ThreadPoolExecutor
sys.argv = [sys.argv[0], "none"] + sys.argv[1:]
exec(compile(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "run.py"), encoding="utf-8").read().replace("__file__", repr(os.path.abspath(__file__))), "run.py", "exec"))
OUTF = OUT + "angle.jsonl"
done = set(json.loads(x)["id"] for x in open(OUTF, encoding="utf-8")) if os.path.exists(OUTF) else set()
Q1 = "紅框裡的{d}，你現在是從它的哪一面看它？只回一個詞：正面／側面／背面"
Q2 = "紅框裡的{d}，在你視線的哪裡？只回一個詞：正前方／偏左／偏右"
jobs = []
for obj, v in L.items():
    for name, box, ang in v["items"]:
        if not box:
            continue
        cx = (box[0] + box[2]) / 2
        where = "偏左" if cx < 0.4 else "偏右" if cx > 0.6 else "正前方"
        for eff in ("快思", "中想"):
            if ang:
                jobs.append((obj, name, box, "面", ang, eff))
            jobs.append((obj, name, box, "位置", where, eff))


def one(j):
    obj, name, box, kind, gold, eff = j
    rid = "|".join([obj, name, kind, eff])
    if rid in done:
        return
    q = (Q1 if kind == "面" else Q2).format(d=L[obj]["desc"])
    c = [{"type": "image_url", "image_url": {"url": T.img_uri(marked(img(obj, name), box))}}, {"type": "text", "text": q}]
    if eff == "快思":
        raw, dt = ask("你是遊戲畫面的眼睛。不要思考，直接回答。", c)
    else:
        raw, dt = ask("你是遊戲畫面的眼睛。先想清楚再回答，最後一行只寫答案那個詞。", c, effort="中想", max_tokens=3000, limit=90)
    tail = raw.strip().split("</think>")[-1][-30:]
    ans = next((w for w in ("正面", "側面", "背面", "正前方", "偏左", "偏右") if w in tail), "")
    r = {"id": rid, "obj": obj, "test": name, "kind": kind, "eff": eff, "gold": gold, "ans": ans,
         "ok": ans == (gold + "面" if kind == "面" else gold), "秒": round(dt, 1)}
    with LOCK:
        open(OUTF, "a", encoding="utf-8").write(json.dumps(r, ensure_ascii=False) + "\n")


with ThreadPoolExecutor(3) as ex:
    list(ex.map(one, jobs))
print("angle done", len(jobs))
