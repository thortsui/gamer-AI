# -*- coding: utf-8 -*-
"""
獨立驗證.py — 跟遊戲代理工具完全無關的「鏡頭轉角／走路放大」驗證器（10/26）

目的：代理的工具自己會宣稱「這次轉了幾度」「中央放大幾倍」。這支程式用另一套方法
（ORB 特徵點 ＋ BFMatcher ＋ RANSAC ＋ 針孔模型）從前後兩張截圖算出真值，拿來對照工具宣稱的值，
確認工具到底準不準。不 import 任何代理程式，也不改任何既有檔案。

用法（在「gamer AI 工具版」資料夾下執行）：
    python 工具版/獨立驗證.py --selftest
        拿 專案/動物醫院/記憶/畫面/ 最近 10 張 *_定時.jpg 做合成純旋轉（±5/10/20/30/45 度）、
        合成抬頭低頭（±5/10 度）、合成中央放大（1.1/1.3/1.6 倍），印出估計誤差表（中位、最大）。
        目標：轉角中位誤差 <2 度、放大誤差 <0.1。
    python 工具版/獨立驗證.py --report
        讀 專案/動物醫院/記憶/工具版/驗證/驗證.jsonl（代理每次轉／走都會寫一筆＋前後兩張圖），
        對每筆算真值、跟工具宣稱比，寫成 工具版/獨立驗證_報告.md（每個工具一張表＋最離譜 10 筆）。
    python 工具版/獨立驗證.py --pair A.jpg B.jpg
        手動看兩張圖之間的轉角、抬頭、放大。
    可加 --akaze 改用 AKAZE 特徵（較慢、特徵點較穩），--limit N 只看最後 N 筆。

方法（純旋轉針孔模型）：
    垂直視角 FOV 70°、畫面寬高比 ≈ 1920:991。焦距 f = (H/2)/tan(35°)。
    每個特徵點 (x, y) → 水平角 a = atan((x/W−0.5)·2·tan35°·W/H)、垂直角 b = atan(−(y/H−0.5)·2·tan35°)（上為正）。
    純旋轉時，所有對應點的 a_before − a_after 都應該一樣 ＝ 鏡頭轉角（正＝右轉，景物往左移）；
    b_before − b_after ＝ 抬頭角（正＝抬頭，景物往下移）。取中位數當估計、四分位距（IQR）當一致性→可信度。
    不取點的區域：畫面邊緣 HUD（上 8%、下 15%、左 6%、右 12%）、第三人稱中央自己的角色（x 0.40～0.60、y 0.35～1.0）、
    全黑區（合成旋轉的黑邊）。靜止不動的對應點（位移 <2px，多半是 HUD 面板）另外算，不混進旋轉估計。
    放大：中央區（x 0.3～0.7、y 0.3～0.7，排除角色）特徵點對的相對距離比取中位數；特徵不夠時退回多尺度 NCC。
"""
from __future__ import annotations

import argparse
import glob
import io
import json
import math
import os
import sys
from collections import defaultdict

import cv2
import numpy as np
from PIL import Image

# ---------- 參數 ----------
V_FOV_DEG = 70.0                       # Roblox 預設垂直視角
HUD_MARGIN = (0.08, 0.15, 0.06, 0.12)  # 上、下、左、右（比例）不取點
SELF_BOX = (0.40, 0.60, 0.35, 1.00)    # 第三人稱自己角色：x0, x1, y0, y1（比例）不取點
ZOOM_BOX = (0.30, 0.70, 0.30, 0.70)    # 放大用的中央區
STATIC_PX = 2.0                        # 位移小於這個 px 的對應點視為「靜止」（HUD）
MIN_INLIERS = 8                        # 低於這個數就說量不出
OK_CONF = 0.5                          # 可信度門檻：達到才算「量得到」

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)
VERIFY_DIR = os.path.join(BASE, "專案", "動物醫院", "記憶", "工具版", "驗證")
SCREEN_DIR = os.path.join(BASE, "專案", "動物醫院", "記憶", "畫面")
REPORT_PATH = os.path.join(HERE, "獨立驗證_報告.md")

USE_AKAZE = False


# ---------- 基本工具 ----------
def load_gray(src) -> np.ndarray:
    """路徑／PIL／ndarray → 灰階 uint8。"""
    if isinstance(src, str):
        data = np.fromfile(src, dtype=np.uint8)      # 路徑有中文，cv2.imread 在 Windows 會失敗
        im = cv2.imdecode(data, cv2.IMREAD_COLOR)
        if im is None:
            raise FileNotFoundError(src)
        return cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)
    if isinstance(src, Image.Image):
        return np.asarray(src.convert("L"))
    a = np.asarray(src)
    if a.ndim == 3:
        return cv2.cvtColor(a, cv2.COLOR_BGR2GRAY)
    return a


def focal_px(W: int, H: int) -> float:
    return (H / 2.0) / math.tan(math.radians(V_FOV_DEG / 2))


def px_to_angles(pts: np.ndarray, W: int, H: int):
    """像素座標 (N,2) → (水平角, 垂直角) 弧度。水平角右為正；垂直角上為正。"""
    t = 2.0 * math.tan(math.radians(V_FOV_DEG / 2))
    xn = (pts[:, 0] / W - 0.5) * t * W / H
    yn = -(pts[:, 1] / H - 0.5) * t
    return np.arctan(xn), np.arctan(yn)


def bearing(pts: np.ndarray, W: int, H: int) -> np.ndarray:
    """像素 → 單位方向向量（相機座標：x 右、y 下、z 前）。"""
    f = focal_px(W, H)
    v = np.stack([(pts[:, 0] - W / 2) / f, (pts[:, 1] - H / 2) / f, np.ones(len(pts))], axis=1)
    return v / np.linalg.norm(v, axis=1, keepdims=True)


def make_mask(W: int, H: int, exclude_self: bool = True, box=None, hud: bool = True) -> np.ndarray:
    """取特徵點的允許區：1＝可取。"""
    m = np.zeros((H, W), np.uint8)
    if box is None:
        t, b, l, r = HUD_MARGIN if hud else (0, 0, 0, 0)
        m[int(t * H):H - int(b * H), int(l * W):W - int(r * W)] = 255
    else:
        x0, x1, y0, y1 = box
        m[int(y0 * H):int(y1 * H), int(x0 * W):int(x1 * W)] = 255
        if hud:
            t, b, l, r = HUD_MARGIN
            mm = np.zeros_like(m)
            mm[int(t * H):H - int(b * H), int(l * W):W - int(r * W)] = 255
            m &= mm
    if exclude_self:
        x0, x1, y0, y1 = SELF_BOX
        m[int(y0 * H):int(y1 * H), int(x0 * W):int(x1 * W)] = 0
    return m


def black_mask(gray: np.ndarray) -> np.ndarray:
    """全黑區（合成旋轉的黑邊）往內多擋 12px。"""
    valid = (gray > 8).astype(np.uint8) * 255
    return cv2.erode(valid, np.ones((25, 25), np.uint8))


def _detector():
    if USE_AKAZE:
        return cv2.AKAZE_create(threshold=0.0008)
    return cv2.ORB_create(nfeatures=4000, scaleFactor=1.2, nlevels=8, fastThreshold=8)


def match_points(g1: np.ndarray, g2: np.ndarray, m1: np.ndarray, m2: np.ndarray):
    """特徵點＋BFMatcher（ratio test）→ (pts1, pts2) 像素座標，float32 (N,2)。"""
    det = _detector()
    k1, d1 = det.detectAndCompute(g1, m1)
    k2, d2 = det.detectAndCompute(g2, m2)
    if d1 is None or d2 is None or len(k1) < 4 or len(k2) < 4:
        return np.zeros((0, 2), np.float32), np.zeros((0, 2), np.float32)
    bf = cv2.BFMatcher(cv2.NORM_HAMMING)
    knn = bf.knnMatch(d1, d2, k=2)
    good = []
    for pair in knn:
        if len(pair) == 2 and pair[0].distance < 0.78 * pair[1].distance:
            good.append(pair[0])
        elif len(pair) == 1:
            good.append(pair[0])
    if not good:
        return np.zeros((0, 2), np.float32), np.zeros((0, 2), np.float32)
    p1 = np.float32([k1[m.queryIdx].pt for m in good])
    p2 = np.float32([k2[m.trainIdx].pt for m in good])
    return p1, p2


def _conf(n: int, iqr_deg: float, n_ok: int = 40, iqr_ok: float = 6.0) -> float:
    """一致性（IQR 越小越好）× 點數 → 0～1 可信度。"""
    if n < MIN_INLIERS:
        return 0.0
    c_iqr = max(0.0, 1.0 - iqr_deg / iqr_ok)
    c_n = min(1.0, n / n_ok)
    return round(c_iqr * (0.5 + 0.5 * c_n), 3)


def _kabsch_rotation(v1: np.ndarray, v2: np.ndarray):
    """v2 ≈ R v1 的最佳旋轉（SVD）。回 R。"""
    Hm = v1.T @ v2
    U, _, Vt = np.linalg.svd(Hm)
    D = np.diag([1, 1, np.sign(np.linalg.det(Vt.T @ U.T))])
    return Vt.T @ D @ U.T


# ---------- 1. 轉角／抬頭 ----------
def estimate_yaw_pitch(before, after, third_person: bool = True):
    """
    前後兩張圖 → (yaw_deg, pitch_deg, n_inliers, 可信度, 診斷dict)。
    yaw 正＝鏡頭右轉（景物往左移）；pitch 正＝抬頭（景物往下移）。量不出時 yaw/pitch 為 None。
    """
    g1, g2 = load_gray(before), load_gray(after)
    if g1.shape != g2.shape:
        g2 = cv2.resize(g2, (g1.shape[1], g1.shape[0]))
    H, W = g1.shape
    base = make_mask(W, H, exclude_self=third_person)
    m1 = base & black_mask(g1)
    m2 = base & black_mask(g2)
    p1, p2 = match_points(g1, g2, m1, m2)
    diag = {"matches": int(len(p1)), "static": 0, "moving_inliers": 0, "iqr_yaw": None, "iqr_pitch": None, "total_rot": None}
    if len(p1) < MIN_INLIERS:
        return None, None, 0, 0.0, diag

    disp = np.linalg.norm(p2 - p1, axis=1)
    static = disp < STATIC_PX
    diag["static"] = int(static.sum())
    mp1, mp2 = p1[~static], p2[~static]

    # RANSAC：純旋轉 ⇔ 單應性（H = K R K⁻¹）
    inl = None
    if len(mp1) >= MIN_INLIERS:
        Hm, msk = cv2.findHomography(mp1, mp2, cv2.RANSAC, 4.0, maxIters=3000, confidence=0.995)
        if Hm is not None and msk is not None:
            inl = msk.ravel().astype(bool)
    if inl is None or inl.sum() < MIN_INLIERS:
        # 動的點太少：如果靜止點很多就是根本沒轉
        if static.sum() >= 20:
            diag["moving_inliers"] = 0
            return 0.0, 0.0, int(static.sum()), _conf(int(static.sum()), 0.0), diag
        return None, None, int(0 if inl is None else inl.sum()), 0.0, diag

    q1, q2 = mp1[inl], mp2[inl]
    n = int(len(q1))
    diag["moving_inliers"] = n
    a1, b1 = px_to_angles(q1, W, H)
    a2, b2 = px_to_angles(q2, W, H)
    dyaw = np.degrees(a1 - a2)
    dpitch = np.degrees(b1 - b2)
    yaw = float(np.median(dyaw))
    pitch = float(np.median(dpitch))
    iqr_y = float(np.subtract(*np.percentile(dyaw, [75, 25])))
    iqr_p = float(np.subtract(*np.percentile(dpitch, [75, 25])))
    diag["iqr_yaw"], diag["iqr_pitch"] = round(iqr_y, 2), round(iqr_p, 2)
    # 軸無關的總轉角（Kabsch）：純轉時應該 ≈ sqrt(yaw²+pitch²)；差很多＝不是純旋轉（有走動／視差）
    try:
        R = _kabsch_rotation(bearing(q1, W, H), bearing(q2, W, H))
        diag["total_rot"] = round(math.degrees(math.acos(max(-1.0, min(1.0, (np.trace(R) - 1) / 2)))), 2)
    except Exception:  # noqa: BLE001
        pass
    # 靜止點很多而動的點很少 → 多半是 HUD 以外真的沒動，動的那幾點是雜訊
    if static.sum() >= 3 * n and static.sum() >= 30 and abs(yaw) < 3 and abs(pitch) < 3:
        return 0.0, 0.0, int(static.sum()), _conf(int(static.sum()), 0.0), diag
    conf = _conf(n, iqr_y)
    return round(yaw, 2), round(pitch, 2), n, conf, diag


# ---------- 2. 放大倍數 ----------
def _ncc_zoom(g1: np.ndarray, g2: np.ndarray, third_person: bool):
    """多尺度 NCC 退路：把 before 中央小塊縮放成各種倍數，在 after 中央找最像的。"""
    H, W = g1.shape
    x0, x1, y0, y1 = ZOOM_BOX
    cx0, cx1 = int((x0 + 0.05) * W), int((x1 - 0.05) * W)
    cy0, cy1 = int((y0 + 0.05) * H), int((y1 - 0.05) * H)
    tpl = g1[cy0:cy1, cx0:cx1]
    if third_person:
        tpl = tpl.copy()
        sx0, sx1, sy0, sy1 = SELF_BOX
        # 角色區塗成灰（NCC 對常數區不敏感）
        tpl[max(0, int(sy0 * H) - cy0):, max(0, int(sx0 * W) - cx0):max(0, int(sx1 * W) - cx0)] = 128
    best, best_z = -1.0, None
    for z in np.arange(0.8, 2.01, 0.02):
        tw, th = int(tpl.shape[1] * z), int(tpl.shape[0] * z)
        if tw < 8 or th < 8 or tw >= W or th >= H:
            continue
        t2 = cv2.resize(tpl, (tw, th), interpolation=cv2.INTER_AREA if z < 1 else cv2.INTER_LINEAR)
        r = cv2.matchTemplate(g2, t2, cv2.TM_CCOEFF_NORMED)
        v = float(r.max())
        if v > best:
            best, best_z = v, float(z)
    if best < 0.5:
        return None, 0.0, {"ncc": round(best, 3)}
    return round(best_z, 3), round(min(1.0, (best - 0.5) * 2), 3), {"ncc": round(best, 3), "method": "ncc"}


def estimate_zoom(before, after, third_person: bool = True):
    """
    前後兩張圖 → (zoom, 可信度, 診斷dict)。zoom >1 ＝ after 的中央比 before 放大（靠近了）。
    中央區特徵點對的相對距離比取中位數；特徵不夠退回多尺度 NCC。
    """
    g1, g2 = load_gray(before), load_gray(after)
    if g1.shape != g2.shape:
        g2 = cv2.resize(g2, (g1.shape[1], g1.shape[0]))
    H, W = g1.shape
    x0, x1, y0, y1 = ZOOM_BOX
    # before 取大一點的範圍（放大後 after 中央的東西原本在 before 更靠中間）；after 取中央區
    m1 = make_mask(W, H, third_person, box=(x0 - 0.1, x1 + 0.1, y0 - 0.1, y1 + 0.1)) & black_mask(g1)
    m2 = make_mask(W, H, third_person, box=(x0, x1, y0, y1)) & black_mask(g2)
    p1, p2 = match_points(g1, g2, m1, m2)
    diag = {"matches": int(len(p1)), "inliers": 0}
    if len(p1) >= 6:
        A, msk = cv2.estimateAffinePartial2D(p1, p2, method=cv2.RANSAC, ransacReprojThreshold=3.0, maxIters=3000)
        if A is not None and msk is not None and msk.sum() >= 6:
            inl = msk.ravel().astype(bool)
            q1, q2 = p1[inl], p2[inl]
            n = int(len(q1))
            diag["inliers"] = n
            # 對應點兩兩距離比（抽最多 400 對）
            rng = np.random.default_rng(0)
            idx = rng.integers(0, n, size=(min(400, n * (n - 1) // 2), 2))
            idx = idx[idx[:, 0] != idx[:, 1]]
            d1 = np.linalg.norm(q1[idx[:, 0]] - q1[idx[:, 1]], axis=1)
            d2 = np.linalg.norm(q2[idx[:, 0]] - q2[idx[:, 1]], axis=1)
            ok = d1 > 12
            if ok.sum() >= 5:
                ratios = d2[ok] / d1[ok]
                z = float(np.median(ratios))
                iqr = float(np.subtract(*np.percentile(ratios, [75, 25])))
                diag["iqr"] = round(iqr, 3)
                diag["affine_scale"] = round(float(math.hypot(A[0, 0], A[1, 0])), 3)
                diag["method"] = "features"
                conf = max(0.0, 1.0 - iqr / 0.3) * min(1.0, n / 25)
                if conf >= 0.3:
                    return round(z, 3), round(conf, 3), diag
    z, c, d2_ = _ncc_zoom(g1, g2, third_person)
    diag.update(d2_)
    return z, c, diag


# ---------- 3. 合成資料 ----------
def _K(W, H):
    f = focal_px(W, H)
    return np.array([[f, 0, W / 2], [0, f, H / 2], [0, 0, 1]], dtype=np.float64)


def synth_rotate(gray: np.ndarray, yaw_deg: float, pitch_deg: float = 0.0, jpeg: bool = True) -> np.ndarray:
    """合成純旋轉：p_after = K·R·K⁻¹·p_before。yaw 正＝右轉；pitch 正＝抬頭。黑邊留著（估計時要擋掉）。"""
    H, W = gray.shape
    t, p = math.radians(yaw_deg), math.radians(pitch_deg)
    Ry = np.array([[math.cos(t), 0, -math.sin(t)], [0, 1, 0], [math.sin(t), 0, math.cos(t)]])
    Rx = np.array([[1, 0, 0], [0, math.cos(p), math.sin(p)], [0, -math.sin(p), math.cos(p)]])
    R = Rx @ Ry
    K = _K(W, H)
    Hm = K @ R @ np.linalg.inv(K)
    out = cv2.warpPerspective(gray, Hm, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    return _jpeg(out) if jpeg else out


def synth_zoom(gray: np.ndarray, z: float, jpeg: bool = True) -> np.ndarray:
    """中央裁切再放大 z 倍。"""
    H, W = gray.shape
    cw, ch = W / z, H / z
    x0, y0 = (W - cw) / 2, (H - ch) / 2
    M = np.array([[z, 0, -x0 * z], [0, z, -y0 * z]], dtype=np.float64)
    out = cv2.warpAffine(gray, M, (W, H), flags=cv2.INTER_LINEAR)
    return _jpeg(out) if jpeg else out


def _jpeg(gray: np.ndarray, q: int = 80) -> np.ndarray:
    ok, buf = cv2.imencode(".jpg", gray, [cv2.IMWRITE_JPEG_QUALITY, q])
    return cv2.imdecode(buf, cv2.IMREAD_GRAYSCALE)


def _recent_screens(n: int = 10):
    fs = glob.glob(os.path.join(SCREEN_DIR, "*_定時.jpg"))
    fs.sort(key=os.path.getmtime)
    return fs[-n:]


def selftest(n_img: int = 10):
    files = _recent_screens(n_img)
    if not files:
        print(f"找不到截圖：{SCREEN_DIR}/*_定時.jpg")
        return 1
    print(f"用 {len(files)} 張截圖：{', '.join(os.path.basename(f) for f in files)}")
    yaws = [-45, -30, -20, -10, -5, 5, 10, 20, 30, 45]
    pitches = [-10, -5, 5, 10]
    zooms = [1.1, 1.3, 1.6]
    rows_yaw = defaultdict(list)   # θ → [(err, conf, n)]
    rows_pitch = defaultdict(list)
    rows_zoom = defaultdict(list)
    fail = defaultdict(int)
    for f in files:
        g = load_gray(f)
        for th in yaws:
            a = synth_rotate(g, th)
            y, p, n, c, d = estimate_yaw_pitch(g, a)
            if y is None:
                fail[("yaw", th)] += 1
                continue
            rows_yaw[th].append((abs(y - th), c, n, abs(p)))
        for ph in pitches:
            a = synth_rotate(g, 0.0, ph)
            y, p, n, c, d = estimate_yaw_pitch(g, a)
            if p is None:
                fail[("pitch", ph)] += 1
                continue
            rows_pitch[ph].append((abs(p - ph), c, n, abs(y)))
        for z in zooms:
            a = synth_zoom(g, z)
            ze, c, d = estimate_zoom(g, a)
            if ze is None:
                fail[("zoom", z)] += 1
                continue
            rows_zoom[z].append((abs(ze - z), c, d.get("inliers", 0)))

    def _tab(title, rows, unit, key_fmt):
        print(f"\n{title}")
        print("| 真值 | 筆數 | 中位誤差 | 最大誤差 | 平均可信度 | 平均點數 | 另一軸誤差中位 | 量不出 |")
        print("|---|---|---|---|---|---|---|---|")
        all_err = []
        for k in sorted(rows):
            r = rows[k]
            errs = [x[0] for x in r]
            all_err += errs
            other = f"{np.median([x[3] for x in r]):.1f}" if len(r[0]) > 3 else "-"
            nf = fail.get((title.split("（")[0].strip("合成"), k), 0)
            print(f"| {key_fmt(k)} | {len(r)} | {np.median(errs):.2f}{unit} | {max(errs):.2f}{unit} | {np.mean([x[1] for x in r]):.2f} | {np.mean([x[2] for x in r]):.0f} | {other} | {nf} |")
        if all_err:
            print(f"全部：中位 {np.median(all_err):.2f}{unit}、90 百分位 {np.percentile(all_err, 90):.2f}{unit}、最大 {max(all_err):.2f}{unit}")
        return all_err

    e_y = _tab("合成純旋轉（yaw）", rows_yaw, "°", lambda k: f"{k:+d}°")
    e_p = _tab("合成抬頭低頭（pitch）", rows_pitch, "°", lambda k: f"{k:+d}°")
    e_z = _tab("合成中央放大（zoom）", rows_zoom, "", lambda k: f"×{k}")
    tot_fail = sum(fail.values())
    print(f"\n量不出的次數：{tot_fail}（{dict((f'{a}{b}', c) for (a, b), c in fail.items())}）" if tot_fail else "\n量不出的次數：0")
    ok_y = bool(e_y) and np.median(e_y) < 2
    ok_z = bool(e_z) and max(e_z) < 0.1
    print(f"目標：轉角中位誤差 <2° → {'達標' if ok_y else '未達標'}；放大誤差 <0.1 → {'達標' if ok_z else '未達標'}")
    return 0 if (ok_y and ok_z) else 2


# ---------- 4. 對照報告 ----------
def _f(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _stats(diffs):
    d = [abs(x) for x in diffs if x is not None]
    if not d:
        return "-", "-", 0
    return f"{np.median(d):.1f}", f"{np.percentile(d, 90):.1f}", len(d)


def report(limit: int | None = None, third_person: bool = True):
    jl = os.path.join(VERIFY_DIR, "驗證.jsonl")
    if not os.path.exists(jl):
        print(f"還沒有資料：{jl}")
        return 1
    recs = []
    with open(jl, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    recs.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    if limit:
        recs = recs[-limit:]
    results = []   # 每筆：rec + 真值
    missing = 0
    for r in recs:
        tag = r.get("檔")
        b = os.path.join(VERIFY_DIR, f"{tag}_before.jpg")
        a = os.path.join(VERIFY_DIR, f"{tag}_after.jpg")
        if not (os.path.exists(b) and os.path.exists(a)):
            missing += 1
            continue
        tool = r.get("工具")
        out = dict(r)
        try:
            y, p, n, c, d = estimate_yaw_pitch(b, a, third_person)
            out.update({"真yaw": y, "真pitch": p, "n": n, "可信": c, "診": d})
            if tool == "走":
                z, zc, zd = estimate_zoom(b, a, third_person)
                out.update({"真放大": z, "放大可信": zc, "放大診": zd})
        except Exception as e:  # noqa: BLE001
            out.update({"真yaw": None, "真pitch": None, "n": 0, "可信": 0.0, "診": {"錯誤": str(e)}})
        results.append(out)

    by_tool = defaultdict(list)
    for o in results:
        by_tool[o.get("工具")].append(o)

    L = []
    L.append(f"# 獨立驗證報告（{len(results)} 筆，{missing} 筆缺圖）\n")
    L.append("方法：ORB 特徵點＋RANSAC＋針孔模型，跟代理工具無關（見 工具版/獨立驗證.py 頂端）。")
    L.append("「真值」＝本程式估的；「量得到」＝可信度 ≥ 0.5；轉角正＝右轉。宣稱與真值的差都取絕對值。\n")

    worst = []   # (差, tag, 工具, 宣稱文字, 真值文字)

    def _measurable(o):
        return o.get("真yaw") is not None and (o.get("可信") or 0) >= OK_CONF

    for tool in ("轉", "轉段", "視角移動到", "走"):
        rs = by_tool.get(tool, [])
        L.append(f"## {tool}（{len(rs)} 筆）\n")
        if not rs:
            L.append("沒有資料。\n")
            continue
        meas = [o for o in rs if _measurable(o)] if tool != "走" else [o for o in rs if o.get("真放大") is not None and (o.get("放大可信") or 0) >= 0.3]
        L.append(f"真值量得到：{len(meas)}／{len(rs)}（{100 * len(meas) / len(rs):.0f}%）\n")
        if tool in ("轉", "轉段"):
            cols = ["送出度", "標點度", "整張度"] + (["用的"] if tool == "轉段" else [])
            L.append("| 宣稱欄位 | 有值筆數 | 跟真值差 中位 | 90 百分位 | 差 >5° 筆數 | 宣稱「量不出」筆數 | 其中真值其實量得到 |")
            L.append("|---|---|---|---|---|---|---|")
            for col in cols:
                diffs, big, none_n, none_meas = [], 0, 0, 0
                for o in rs:
                    v = _f(o.get(col))
                    if v is None:
                        none_n += 1
                        if _measurable(o):
                            none_meas += 1
                        continue
                    if _measurable(o):
                        dd = v - o["真yaw"]
                        diffs.append(dd)
                        if abs(dd) > 5:
                            big += 1
                med, p90, n = _stats(diffs)
                pct = f"{none_meas}／{none_n}（{100 * none_meas / none_n:.0f}%）" if none_n else "0／0"
                L.append(f"| {col} | {n} | {med} | {p90} | {big} | {none_n} | {pct} |")
            # 宣稱一致但真值差 >5
            claim_ok_bad = 0
            claim_ok = 0
            for o in rs:
                want, lm = _f(o.get("送出度")), _f(o.get("標點度"))
                if want is None or lm is None:
                    continue
                if abs(lm - want) <= max(5.0, 0.15 * abs(want)):
                    claim_ok += 1
                    if _measurable(o) and abs(o["真yaw"] - want) > 5:
                        claim_ok_bad += 1
            L.append(f"\n工具宣稱「標點跟送出一致」的 {claim_ok} 筆裡，真值跟送出差 >5° 的：{claim_ok_bad} 筆。")
            # 真值 vs 送出 總表
            diffs = [_f(o.get("送出度")) - o["真yaw"] for o in meas if _f(o.get("送出度")) is not None]
            med, p90, n = _stats(diffs)
            L.append(f"送出度 vs 真值（{n} 筆）：差中位 {med}°、90 百分位 {p90}°；實際轉過頭（|真|>|送出|+5）{sum(1 for o in meas if _f(o.get('送出度')) is not None and abs(o['真yaw']) > abs(_f(o.get('送出度'))) + 5)} 筆、轉不夠（|真|<|送出|−5）{sum(1 for o in meas if _f(o.get('送出度')) is not None and abs(o['真yaw']) < abs(_f(o.get('送出度'))) - 5)} 筆、方向相反 {sum(1 for o in meas if _f(o.get('送出度')) and o['真yaw'] * _f(o.get('送出度')) < 0 and abs(o['真yaw']) > 3)} 筆。\n")
            for o in meas:
                claim = _f(o.get("用的")) if tool == "轉段" and _f(o.get("用的")) is not None else (_f(o.get("標點度")) if _f(o.get("標點度")) is not None else _f(o.get("送出度")))
                if claim is not None:
                    worst.append((abs(claim - o["真yaw"]), o.get("檔"), tool,
                                  f"送出 {_f(o.get('送出度')):+.0f}／標點 {o.get('標點度')}／整張 {o.get('整張度')}" + (f"／用的 {o.get('用的')}" if tool == "轉段" else ""),
                                  f"yaw {o['真yaw']:+.1f}°（pitch {o['真pitch']:+.1f}°，{o['n']} 點，可信 {o['可信']:.2f}）"))
        elif tool == "視角移動到":
            L.append("| 宣稱欄位 | 有值筆數 | 跟真值差 中位 | 90 百分位 | 差 >5° 筆數 |")
            L.append("|---|---|---|---|---|")
            for col, key in (("送出度", "真yaw"), ("送出上下", "真pitch")):
                diffs = [_f(o.get(col)) - o[key] for o in meas if _f(o.get(col)) is not None]
                med, p90, n = _stats(diffs)
                L.append(f"| {col} | {n} | {med} | {p90} | {sum(1 for d in diffs if abs(d) > 5)} |")
            L.append("")
            for o in meas:
                v = _f(o.get("送出度"))
                if v is not None:
                    worst.append((abs(v - o["真yaw"]), o.get("檔"), tool, f"送出 {v:+.0f}／上下 {o.get('送出上下')}",
                                  f"yaw {o['真yaw']:+.1f}°（pitch {o['真pitch']:+.1f}°，{o['n']} 點，可信 {o['可信']:.2f}）"))
        elif tool == "走":
            diffs, none_n, none_meas, big = [], 0, 0, 0
            for o in rs:
                v = _f(o.get("放大"))
                ok = o.get("真放大") is not None and (o.get("放大可信") or 0) >= 0.3
                if v is None:
                    none_n += 1
                    none_meas += ok
                    continue
                if ok:
                    diffs.append(v - o["真放大"])
                    if abs(v - o["真放大"]) > 0.2:
                        big += 1
                    worst.append((abs(v - o["真放大"]), o.get("檔"), tool, f"放大 {v:.2f}（走 {o.get('秒')} 秒）",
                                  f"放大 {o['真放大']:.2f}（可信 {o['放大可信']:.2f}，{o.get('放大診', {}).get('method', '?')}）"))
            med, p90, n = _stats(diffs)
            L.append("| 宣稱欄位 | 有值筆數 | 跟真值差 中位 | 90 百分位 | 差 >0.2 筆數 | 宣稱「量不出」筆數 | 其中真值其實量得到 |")
            L.append("|---|---|---|---|---|---|---|")
            pct = f"{none_meas}／{none_n}（{100 * none_meas / none_n:.0f}%）" if none_n else "0／0"
            L.append(f"| 放大 | {n} | {med} | {p90} | {big} | {none_n} | {pct} |\n")

    worst.sort(key=lambda t: -t[0])
    L.append("## 最離譜的 10 筆（宣稱 vs 真值）\n")
    L.append("| 差 | tag | 工具 | 宣稱 | 真值 |")
    L.append("|---|---|---|---|---|")
    for d, tag, tool, c, t in worst[:10]:
        L.append(f"| {d:.1f} | {tag} | {tool} | {c} | {t} |")
    L.append("")
    # 每筆明細（方便翻圖）
    L.append("## 每筆明細\n")
    L.append("| tag | 工具 | 宣稱 | 真 yaw | 真 pitch | 點數 | 可信 | 真放大 |")
    L.append("|---|---|---|---|---|---|---|---|")
    for o in results:
        tool = o.get("工具")
        if tool in ("轉", "轉段"):
            c = f"送出 {o.get('送出度')}／標點 {o.get('標點度')}／整張 {o.get('整張度')}" + (f"／用的 {o.get('用的')}" if tool == "轉段" else "")
        elif tool == "視角移動到":
            c = f"送出 {o.get('送出度')}／上下 {o.get('送出上下')}"
        else:
            c = f"放大 {o.get('放大')}／秒 {o.get('秒')}"
        yv = "量不出" if o.get("真yaw") is None else f"{o['真yaw']:+.1f}"
        pv = "-" if o.get("真pitch") is None else f"{o['真pitch']:+.1f}"
        zv = "" if tool != "走" else ("量不出" if o.get("真放大") is None else f"{o['真放大']:.2f}")
        L.append(f"| {o.get('檔')} | {tool} | {c} | {yv} | {pv} | {o.get('n')} | {o.get('可信'):.2f} | {zv} |")
    text = "\n".join(L) + "\n"
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write(text)
    print(text)
    print(f"→ 已寫 {REPORT_PATH}")
    return 0


# ---------- 入口 ----------
def main():
    global USE_AKAZE
    ap = argparse.ArgumentParser(description="獨立驗證：用特徵點從前後截圖算鏡頭轉角／放大，對照代理工具宣稱的值")
    ap.add_argument("--selftest", action="store_true", help="合成資料自我測試")
    ap.add_argument("--report", action="store_true", help="讀 驗證.jsonl 出報告")
    ap.add_argument("--pair", nargs=2, metavar=("BEFORE", "AFTER"), help="手動看兩張圖")
    ap.add_argument("--akaze", action="store_true", help="改用 AKAZE 特徵")
    ap.add_argument("--limit", type=int, default=None, help="--report 只看最後 N 筆")
    ap.add_argument("--first-person", action="store_true", help="不排除中央角色區（第一人稱）")
    ap.add_argument("--n-img", type=int, default=10, help="--selftest 用幾張截圖")
    args = ap.parse_args()
    USE_AKAZE = args.akaze
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    if args.selftest:
        return selftest(args.n_img)
    if args.report:
        return report(args.limit, not args.first_person)
    if args.pair:
        y, p, n, c, d = estimate_yaw_pitch(args.pair[0], args.pair[1], not args.first_person)
        z, zc, zd = estimate_zoom(args.pair[0], args.pair[1], not args.first_person)
        print(f"yaw {y}°（正＝右轉） pitch {p}°（正＝抬頭） 點數 {n} 可信 {c} 診斷 {d}")
        print(f"放大 {z} 可信 {zc} 診斷 {zd}")
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
