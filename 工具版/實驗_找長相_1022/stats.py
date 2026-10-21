import json, os, collections, sys
OUT = os.path.dirname(os.path.abspath(__file__)) + "/"
R = []
for x in open(OUT + "results.jsonl", encoding="utf-8", errors="replace"):
    try:
        R.append(json.loads(x))
    except ValueError:
        pass


def acc(rs):
    if not rs:
        return "  -  "
    p = [r for r in rs if r["pos"]]; n = [r for r in rs if not r["pos"]]
    a = sum(r["ok"] for r in rs) / len(rs)
    hp = sum(r["ok"] for r in p) / len(p) if p else 0
    fn = sum(r["ok"] for r in n) / len(n) if n else 0
    return f"{a * 100:3.0f}%（有的找對 {hp * 100:3.0f}%、沒有的答對 {fn * 100:3.0f}%，n={len(rs)}）"


print("== 主實驗：參考圖張數 × 角度（金框、固定提示詞）")
main = [r for r in R if r["src"] == "金框" and r["pr"] == "固定" and r["mode"] in ("多角度", "同角度")]
objs = sorted(set(r["obj"] for r in R))
for mode in ("多角度", "同角度"):
    print(" ", mode)
    for K in (7, 4, 2, 1, 0):
        rs = [r for r in main if r["K"] == K and (r["mode"] == mode or K == 0)]
        print(f"    K={K}: 全部 {acc(rs)}")
        for o in objs:
            print(f"        {o:10s} {acc([r for r in rs if r['obj'] == o])}")
print("== 同角度：考題跟參考圖同一面 vs 不同面")
for K in (7, 4, 2, 1):
    rs = [r for r in main if r["K"] == K and r["mode"] == "同角度" and r["pos"] and r["test_angle"]]
    same = [r for r in rs if r["test_angle"] == r["ref_angles"][0]]
    diff = [r for r in rs if r["test_angle"] != r["ref_angles"][0]]
    print(f"  K={K}: 同一面 {acc(same)}｜不同面 {acc(diff)}")
print("== 框的來源 × 提示詞（多角度）")
for K in (1, 2, 4):
    for src in ("金框", "快框", "想框"):
        for pr in ("固定", "自己", "名字"):
            rs = [r for r in R if r["K"] == K and r["mode"] == "多角度" and r["src"] == src and r["pr"] == pr and r["seed"] < 2]
            print(f"  K={K} {src} {pr}提示詞: {acc(rs)}")
if os.path.exists(OUT + "boxes.json"):
    B = json.load(open(OUT + "boxes.json", encoding="utf-8"))
    print("== 他自己框的品質（有東西的圖）")
    for src in ("快框", "想框"):
        v = [b for k, b in B.items() if k.endswith(src)]
        ious = [b["iou"] for b in v if b["iou"] or b["box"]]
        got = sum(1 for b in v if b["box"])
        print(f"  {src}: 有框 {got}/{len(v)}，平均 IoU {sum(ious) / max(1, len(ious)):.2f}，IoU≥0.5 {sum(i >= 0.5 for i in ious)}/{len(ious)}，平均 {sum(b['秒'] for b in v) / max(1, len(v)):.1f} 秒")
if os.path.exists(OUT + "angle.jsonl"):
    A = [json.loads(x) for x in open(OUT + "angle.jsonl", encoding="utf-8")]
    print("== 正視測試")
    for kind in ("面", "位置"):
        for eff in ("快思", "中想"):
            rs = [a for a in A if a["kind"] == kind and a["eff"] == eff]
            if not rs:
                continue
            c = collections.Counter((a["gold"], a["ans"]) for a in rs)
            line = f"  {kind} {eff}: 對 {sum(a['ok'] for a in rs)}/{len(rs)}"
            if kind == "面":
                bi = sum((a["gold"] == "正") == (a["ans"] == "正面") for a in rs)
                line += f"｜正對/不是正對 {bi}/{len(rs)}"
            print(line, dict(c))
