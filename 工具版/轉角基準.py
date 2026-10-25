"""比較幾種開源方法估兩張畫面之間鏡頭轉了幾度：ORB+RANSAC（現用）、SuperPoint/DISK/ALIKED+LightGlue、LoFTR、RAFT 光流。
A) 合成真值：真實截圖照針孔模型轉 yaw/pitch、貼回 HUD 與中央角色、JPEG。B) 今天 73 對真實前後圖：跟送出的度數比。"""
import os, sys, time, json, glob, math, warnings
warnings.filterwarnings("ignore")
os.chdir(r"D:\Documents\ThorWorkSpace\gamer AI 工具版")
sys.path.insert(0, "工具版")
import numpy as np, cv2, torch
import 獨立驗證 as V

dev = "cuda" if torch.cuda.is_available() else "cpu"
OUT = os.path.join("工具版", "轉角基準_結果.json")


def fit(p1, p2, W, H):
    """跟 獨立驗證 一樣：剔除靜止點（HUD）→ RANSAC 單應性 → 每點角度差取中位。回 (yaw, pitch, n, conf)。"""
    if len(p1) < 8:
        return None, None, len(p1), 0.0
    disp = np.linalg.norm(p2 - p1, axis=1)
    mv = disp >= V.STATIC_PX
    n_static = int((~mv).sum())
    r = V._fit_rotation(p1[mv], p2[mv], W, H) if mv.sum() >= 8 else None
    if r is None:
        if n_static >= 20:
            return 0.0, 0.0, n_static, V._conf(n_static, 0.0)
        return None, None, int(mv.sum()), 0.0
    yaw, pitch, n, conf, *_ = r
    if n_static >= 3 * n and n_static >= 30 and abs(yaw) < 3 and abs(pitch) < 3:
        return 0.0, 0.0, n_static, V._conf(n_static, 0.0)
    return yaw, pitch, n, conf


def mask_pts(p1, p2, m1, m2):
    a = m1[np.clip(p1[:, 1].astype(int), 0, m1.shape[0] - 1), np.clip(p1[:, 0].astype(int), 0, m1.shape[1] - 1)] > 0
    b = m2[np.clip(p2[:, 1].astype(int), 0, m2.shape[0] - 1), np.clip(p2[:, 0].astype(int), 0, m2.shape[1] - 1)] > 0
    k = a & b
    return p1[k], p2[k]


def masks(g1, g2):
    H, W = g1.shape
    tp = V._is_third_person(g1, g2, None)
    base = V.make_mask(W, H, exclude_self=tp)
    return base & V.black_mask(g1), base & V.black_mask(g2)


# ---------- 方法 ----------
def m_orb(g1, g2):
    y, p, n, c, _ = V.estimate_yaw_pitch(g1, g2, sweep=False)
    return y, p, n, c


_lg = {}


def _lg_pair(name):
    if name not in _lg:
        from lightglue import LightGlue, SuperPoint, DISK, ALIKED
        ext = {"superpoint": SuperPoint, "disk": DISK, "aliked": ALIKED}[name](max_num_keypoints=2048).eval().to(dev)
        _lg[name] = (ext, LightGlue(features=name).eval().to(dev))
    return _lg[name]


def m_lightglue(name):
    def f(g1, g2):
        from lightglue.utils import rbd
        ext, mat = _lg_pair(name)
        H, W = g1.shape
        m1, m2 = masks(g1, g2)
        with torch.no_grad():
            t1 = torch.from_numpy(g1).float().div(255)[None, None].to(dev).expand(1, 3, H, W) if name != "superpoint" else torch.from_numpy(g1).float().div(255)[None, None].to(dev)
            t2 = torch.from_numpy(g2).float().div(255)[None, None].to(dev).expand(1, 3, H, W) if name != "superpoint" else torch.from_numpy(g2).float().div(255)[None, None].to(dev)
            f1, f2 = ext.extract(t1), ext.extract(t2)
            mm = mat({"image0": f1, "image1": f2})
            f1, f2, mm = [rbd(x) for x in (f1, f2, mm)]
            idx = mm["matches"]
            p1 = f1["keypoints"][idx[:, 0]].cpu().numpy()
            p2 = f2["keypoints"][idx[:, 1]].cpu().numpy()
        p1, p2 = mask_pts(p1, p2, m1, m2)
        return fit(p1, p2, W, H)
    return f


_loftr = {}


def m_loftr(g1, g2):
    import kornia.feature as KF
    if "m" not in _loftr:
        _loftr["m"] = KF.LoFTR(pretrained="outdoor").eval().to(dev)
    H, W = g1.shape
    s = 640 / W
    a = cv2.resize(g1, (640, int(H * s))); b = cv2.resize(g2, (640, int(H * s)))
    m1, m2 = masks(g1, g2)
    with torch.no_grad():
        t1 = torch.from_numpy(a).float().div(255)[None, None].to(dev)
        t2 = torch.from_numpy(b).float().div(255)[None, None].to(dev)
        o = _loftr["m"]({"image0": t1, "image1": t2})
    conf = o["confidence"].cpu().numpy()
    p1 = o["keypoints0"].cpu().numpy() / s; p2 = o["keypoints1"].cpu().numpy() / s
    k = conf > 0.5
    p1, p2 = mask_pts(p1[k], p2[k], m1, m2)
    return fit(p1, p2, W, H)


_raft = {}


def m_raft(g1, g2):
    from torchvision.models.optical_flow import raft_small, Raft_Small_Weights
    if "m" not in _raft:
        _raft["m"] = raft_small(weights=Raft_Small_Weights.DEFAULT).eval().to(dev)
    H, W = g1.shape
    nh, nw = 512, 960
    a = cv2.resize(g1, (nw, nh)); b = cv2.resize(g2, (nw, nh))
    m1, m2 = masks(g1, g2)
    with torch.no_grad():
        t1 = torch.from_numpy(a).float().div(255)[None, None].to(dev).expand(1, 3, nh, nw) * 2 - 1
        t2 = torch.from_numpy(b).float().div(255)[None, None].to(dev).expand(1, 3, nh, nw) * 2 - 1
        flow = _raft["m"](t1, t2)[-1][0].cpu().numpy()   # 2,nh,nw
    ys, xs = np.mgrid[16:nh:24, 16:nw:24]
    fx = flow[0, ys, xs] * (W / nw); fy = flow[1, ys, xs] * (H / nh)
    p1 = np.stack([xs * (W / nw), ys * (H / nh)], -1).reshape(-1, 2).astype(np.float32)
    p2 = p1 + np.stack([fx, fy], -1).reshape(-1, 2).astype(np.float32)
    p1, p2 = mask_pts(p1, p2, m1, m2)
    return fit(p1, p2, W, H)


METHODS = {"ORB+RANSAC（現用）": m_orb, "SuperPoint+LightGlue": m_lightglue("superpoint"), "DISK+LightGlue": m_lightglue("disk"),
           "ALIKED+LightGlue": m_lightglue("aliked"), "LoFTR（kornia outdoor）": m_loftr, "RAFT-small 光流": m_raft}
only = sys.argv[1:] if len(sys.argv) > 1 else None
if only:
    METHODS = {k: v for k, v in METHODS.items() if any(o in k for o in only)}

res = {"synthetic": {}, "real": {}}
# ---------- A) 合成真值 ----------
shots = sorted(glob.glob(os.path.join(V.VERIFY_DIR, "*_before.jpg")))
rng = np.random.RandomState(0)
pick = [shots[i] for i in rng.choice(len(shots), min(14, len(shots)), replace=False)]
cases = []
for sp in pick:
    g = V.load_gray(sp)
    for yaw in (-45, -30, -15, -5, 5, 15, 30, 45):
        for pitch in (0, 8):
            cases.append((sp, g, yaw, pitch, V.synth_hud(g, V.synth_rotate(g, yaw, pitch))))
print(f"合成 {len(cases)} 例、真實 {len(shots)} 對、裝置 {dev}", flush=True)
for name, fn in METHODS.items():
    errs, fails, big, ts = [], 0, 0, []
    try:
        fn(cases[0][1], cases[0][4])   # 暖機
    except Exception as e:
        print(name, "不能用：", repr(e)[:200]); res["synthetic"][name] = {"error": repr(e)[:200]}; continue
    for sp, g, yaw, pitch, g2 in cases:
        t = time.time()
        try:
            y, p, n, c = fn(g, g2)
        except Exception as e:
            y, p, n, c = None, None, 0, 0.0
        ts.append(time.time() - t)
        if y is None or c < V.OK_CONF:
            fails += 1; continue
        e = abs(y - yaw); errs.append(e); big += e > 5
    errs = np.array(errs) if errs else np.array([99.0])
    r = {"n": len(cases), "量不出": fails, "中位誤差": round(float(np.median(errs)), 2), "90百分位": round(float(np.percentile(errs, 90)), 2),
         "最大": round(float(errs.max()), 2), "差>5°": int(big), "每對秒": round(float(np.median(ts)), 3)}
    res["synthetic"][name] = r
    print("合成", name, r, flush=True)

# ---------- B) 真實 73 對 ----------
recs = {}
for l in open(os.path.join(V.VERIFY_DIR, "驗證.jsonl"), encoding="utf-8"):
    try:
        o = json.loads(l); recs[o["檔"]] = o
    except Exception:
        pass
pairs = []
for bp in shots:
    tag = os.path.basename(bp)[:-len("_before.jpg")]
    ap = bp.replace("_before.jpg", "_after.jpg")
    if os.path.exists(ap) and tag in recs and recs[tag]["工具"] in ("轉段", "視角移動到", "轉"):
        pairs.append((tag, bp, ap, recs[tag]))
vals = {name: {} for name in METHODS}
for name, fn in METHODS.items():
    if "error" in res["synthetic"].get(name, {}):
        continue
    d, fails, ts = [], 0, []
    for tag, bp, ap, rec in pairs:
        g1, g2 = V.load_gray(bp), V.load_gray(ap)
        t = time.time()
        try:
            y, p, n, c = fn(g1, g2)
        except Exception:
            y, c = None, 0.0
        ts.append(time.time() - t)
        sent = rec.get("送出度")
        if y is None or c < V.OK_CONF:
            fails += 1; vals[name][tag] = None; continue
        vals[name][tag] = round(float(y), 1)
        if sent is not None:
            d.append(abs(y - sent))
    d = np.array(d) if d else np.array([99.0])
    r = {"n": len(pairs), "量不出": fails, "跟送出差 中位": round(float(np.median(d)), 2), "90百分位": round(float(np.percentile(d, 90)), 2), "每對秒": round(float(np.median(ts)), 3)}
    res["real"][name] = r
    print("真實", name, r, flush=True)
# 方法之間的一致：跟 ORB 差
base = vals.get("ORB+RANSAC（現用）", {})
for name in vals:
    if name == "ORB+RANSAC（現用）" or not vals[name]:
        continue
    dd = [abs(vals[name][t] - base[t]) for t in vals[name] if vals[name][t] is not None and base.get(t) is not None]
    both_fail = sum(1 for t in vals[name] if vals[name][t] is None and base.get(t) is None)
    res["real"][name]["跟ORB差 中位"] = round(float(np.median(dd)), 2) if dd else None
    res["real"][name]["ORB量不出但它量到"] = sum(1 for t in vals[name] if vals[name][t] is not None and base.get(t) is None)
# 大廳那六對（標點只量到一半）
lobby = ["003058_49_轉段", "003154_00_轉段", "003241_32_轉段", "003244_00_轉段", "003536_35_轉段", "003538_76_轉段"]
res["lobby6"] = {name: {t: vals[name].get(t) for t in lobby} for name in vals}
res["lobby6_sent"] = {t: recs[t].get("送出度") for t in lobby if t in recs}
res["lobby6_tool"] = {t: recs[t].get("用的") for t in lobby if t in recs}
json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("→", OUT)
