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
        對每筆算真值、跟工具宣稱比，寫成 工具版/獨立驗證_報告.md（每個工具一張表＋最離譜 10 筆＋每筆明細）。
    python 工具版/獨立驗證.py --pair A.jpg B.jpg
        手動看兩張圖之間的轉角、抬頭、放大。
    選項：--akaze 改用 AKAZE 特徵（較慢、較穩）；--limit N 只看最後 N 筆；
          --first-person／--third-person 強制不排除／排除中央角色區（預設自動：中央那塊前後沒動就當第三人稱）。

方法（純旋轉針孔模型）：
    垂直視角 FOV 70°、畫面寬高比 ≈ 1920:991。焦距 f = (H/2)/tan(35°)。
    每個特徵點 (x, y) → 水平角 a = atan((x/W−0.5)·2·tan35°·W/H)、垂直角 b = atan(−(y/H−0.5)·2·tan35°)（上為正）。
    純旋轉時，所有對應點的 a_before − a_after 都應該一樣 ＝ 鏡頭轉角（正＝右轉，景物往左移）；
    b_before − b_after ＝ 抬頭角（正＝抬頭，景物往下移）。取中位數當估計、四分位距（IQR）當一致性 → 可信度。
    不取點的區域：畫面邊緣 HUD（上 8%、下 15%、左 6%、右 12%）、第三人稱中央自己的角色（x 0.40～0.60、y 0.35～1.0）、
    全黑區（合成旋轉的黑邊）。前後完全沒動的對應點（位移 <2px，多半是 HUD 面板）另外算，不混進旋轉估計。
    轉很大（≥30°）時直接比對常找不到足夠的點，就先把 before 預轉 ±15/30/45/60 度再比（殘差＋預轉角＝總轉角）。
    每個候選答案都做光度驗證：把 before 照那個轉角扭過去疊在 after 上算 NCC，挑疊得最好的；點少又疊不上的把可信度打折
    （重複的椅子／螢幕會湊出一致但錯的假點群，這一步擋掉）。點很多（≥80）且一致就不靠光度（貼著粗糙牆面 NCC 本來就低）。
    放大：中央區（x/y 0.2～0.8，排除角色與 HUD、排除前後沒動的點）特徵點對的相對距離比取中位數（RANSAC 相似轉換挑內點），
    走路順便轉頭的先把 before 反轉回去再量；真實走路有視差（遠近東西放大倍數不同）、相似轉換常湊不出來，
    就改用多尺度 NCC（before 中央塊放大成各種倍數在 after 中央附近找；第一人稱用 0.38～0.62 的小塊，第三人稱用扣掉角色的環）。
    偵測前都先做 CLAHE 局部對比增強（暗房間才找得到特徵）。--akaze 在自測裡反而比 ORB 差很多，留著只是備用。

自測結果（10/26，10 張截圖 × 30 種合成）：純旋轉 yaw 中位誤差 0.02°、最大 0.48°；pitch 0.01°；放大中位 0.00、最大 0.21；
    300 次裡 5 次說量不出（都是 30～45° 的平牆暗房），0 次「有信心但錯」。
限制：① 真實鏡頭不是純旋轉（第三人稱是繞著角色轉、貼牆時 Roblox 會把鏡頭往前推），貼近東西時轉角會混到視差；
    ② 走路的「中央放大」本身定義模糊（中央有遠有近），NCC 量的是紋理最多的那一層，只能當參考；
    ③ 重複花紋（磁磚、整排一樣的椅子）可能湊出假答案，靠光度驗證擋、但不保證；④ 畫面幾乎全是平牆／黑暗時量不出。
"""
from __future__ import annotations

import argparse
import glob
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
ZOOM_BOX = (0.20, 0.80, 0.20, 0.80)    # 放大用的中央區（角色與 HUD 另外扣掉）
STATIC_PX = 2.0                        # 位移小於這個 px 的對應點視為「靜止」（HUD）
MIN_INLIERS = 8                        # 低於這個數就說量不出
OK_CONF = 0.5                          # 可信度門檻：達到才算「量得到」
ZOOM_OK_CONF = 0.5                     # 放大的可信度門檻（內點 ≥15 且距離比一致才到得了）
PRE_TURNS = (-60, -45, -30, -15, 15, 30, 45, 60)   # 直接比對不夠好時的預轉角
CENTER_STATIC_NCC = 0.75               # 中央角色區前後 NCC 高於此 → 當第三人稱（角色釘在畫面上）
CENTER_BOX = (0.43, 0.57, 0.40, 0.85)  # 判斷第三人稱用的中央內框（比 SELF_BOX 小一圈，只看角色本體）

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


_CLAHE = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))


def enhance(gray: np.ndarray) -> np.ndarray:
    """偵測特徵前先做局部對比增強（CLAHE）：暗房間／平的牆原本找不到幾個點，加了之後 10 張合成全過。"""
    return _CLAHE.apply(gray)


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


def _box_mask(W: int, H: int, box) -> np.ndarray:
    x0, x1, y0, y1 = box
    m = np.zeros((H, W), np.uint8)
    m[max(0, int(y0 * H)):min(H, int(y1 * H)), max(0, int(x0 * W)):min(W, int(x1 * W))] = 255
    return m


def make_mask(W: int, H: int, exclude_self: bool, box=None) -> np.ndarray:
    """取特徵點的允許區：255＝可取。永遠扣 HUD 邊緣；box 給了就只取 box 內；exclude_self 扣中央角色。"""
    t, b, l, r = HUD_MARGIN
    m = _box_mask(W, H, (l, 1 - r, t, 1 - b))
    if box is not None:
        m &= _box_mask(W, H, box)
    if exclude_self:
        m &= ~_box_mask(W, H, SELF_BOX)
    return m


def black_mask(gray: np.ndarray) -> np.ndarray:
    """全黑區（合成旋轉的黑邊）往內多擋 12px。"""
    valid = (gray > 8).astype(np.uint8) * 255
    return cv2.erode(valid, np.ones((25, 25), np.uint8))


def center_static(g1: np.ndarray, g2: np.ndarray) -> float:
    """中央角色區前後的 NCC：高＝那塊釘在畫面上（第三人稱的自己）。"""
    H, W = g1.shape
    x0, x1, y0, y1 = CENTER_BOX
    a = g1[int(y0 * H):int(y1 * H), int(x0 * W):int(x1 * W)].astype(np.float32)
    b = g2[int(y0 * H):int(y1 * H), int(x0 * W):int(x1 * W)].astype(np.float32)
    a -= a.mean()
    b -= b.mean()
    den = float(np.sqrt((a * a).sum() * (b * b).sum()))
    return float((a * b).sum() / den) if den > 1e-6 else 0.0


def _is_third_person(g1, g2, third_person) -> bool:
    if third_person is None:
        return center_static(g1, g2) >= CENTER_STATIC_NCC
    return bool(third_person)


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


def _K(W, H):
    f = focal_px(W, H)
    return np.array([[f, 0, W / 2], [0, f, H / 2], [0, 0, 1]], dtype=np.float64)


def _rot_H(W, H, yaw_deg, pitch_deg=0.0):
    t, p = math.radians(yaw_deg), math.radians(pitch_deg)
    Ry = np.array([[math.cos(t), 0, -math.sin(t)], [0, 1, 0], [math.sin(t), 0, math.cos(t)]])
    Rx = np.array([[1, 0, 0], [0, math.cos(p), math.sin(p)], [0, -math.sin(p), math.cos(p)]])
    K = _K(W, H)
    return K @ (Rx @ Ry) @ np.linalg.inv(K)


def _punch_static(mask: np.ndarray, pts: np.ndarray, r: int = 12) -> np.ndarray:
    """把靜止點（HUD）附近挖掉。"""
    m = mask.copy()
    for x, y in pts:
        cv2.circle(m, (int(x), int(y)), r, 0, -1)
    return m


# ---------- 1. 轉角／抬頭 ----------
def _verify_ncc(e1: np.ndarray, e2: np.ndarray, yaw: float, pitch: float, m1: np.ndarray, m2: np.ndarray):
    """光度驗證：把 before 照估出來的轉角扭過去疊在 after 上，算重疊區（扣 HUD／角色／黑邊）的 NCC。
    真的轉角整張都疊得上（合成資料 0.3～0.99）；假的點群湊出來的答案疊不上（多半 <0.25）。"""
    H, W = e1.shape
    Hm = _rot_H(W, H, yaw, pitch)
    w1 = cv2.warpPerspective(e1, Hm, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    wm = cv2.warpPerspective(m1, Hm, (W, H), flags=cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    sel = (wm & m2 & black_mask(w1)) > 0
    if sel.sum() < 2000:
        return None
    a = w1[sel].astype(np.float32)
    b = e2[sel].astype(np.float32)
    a -= a.mean()
    b -= b.mean()
    den = float(np.sqrt((a * a).sum() * (b * b).sum()))
    return float((a * b).sum() / den) if den > 1e-6 else None


def _fit_rotation(p1: np.ndarray, p2: np.ndarray, W: int, H: int):
    """對應點 → (yaw, pitch, n, conf, iqr_yaw, iqr_pitch, total_rot)。RANSAC 單應性挑內點。"""
    if len(p1) < MIN_INLIERS:
        return None
    Hm, msk = cv2.findHomography(p1, p2, cv2.RANSAC, 4.0, maxIters=3000, confidence=0.995)
    if Hm is None or msk is None:
        return None
    inl = msk.ravel().astype(bool)
    if inl.sum() < MIN_INLIERS:
        return None
    q1, q2 = p1[inl], p2[inl]
    n = int(len(q1))
    a1, b1 = px_to_angles(q1, W, H)
    a2, b2 = px_to_angles(q2, W, H)
    dyaw = np.degrees(a1 - a2)
    dpitch = np.degrees(b1 - b2)
    yaw, pitch = float(np.median(dyaw)), float(np.median(dpitch))
    iqr_y = float(np.subtract(*np.percentile(dyaw, [75, 25])))
    iqr_p = float(np.subtract(*np.percentile(dpitch, [75, 25])))
    total = None
    try:
        R = _kabsch_rotation(bearing(q1, W, H), bearing(q2, W, H))
        total = round(math.degrees(math.acos(max(-1.0, min(1.0, (np.trace(R) - 1) / 2)))), 2)
    except Exception:  # noqa: BLE001
        pass
    return yaw, pitch, n, _conf(n, iqr_y), round(iqr_y, 2), round(iqr_p, 2), total


def estimate_yaw_pitch(before, after, third_person=None, sweep: bool = True):
    """
    前後兩張圖 → (yaw_deg, pitch_deg, n_inliers, 可信度, 診斷dict)。
    yaw 正＝鏡頭右轉（景物往左移）；pitch 正＝抬頭（景物往下移）。量不出時 yaw/pitch 為 None。
    third_person：None 自動（中央角色區前後沒動就排除）、True 排除、False 不排除。
    sweep：直接比對不夠可信時要不要預轉 ±15～60 度再比（走路的畫面不是純旋轉，預轉會湊出假轉角 → 走 的紀錄關掉）。
    """
    g1, g2 = load_gray(before), load_gray(after)
    if g1.shape != g2.shape:
        g2 = cv2.resize(g2, (g1.shape[1], g1.shape[0]))
    H, W = g1.shape
    tp = _is_third_person(g1, g2, third_person)
    base = make_mask(W, H, exclude_self=tp)
    m1 = base & black_mask(g1)
    m2 = base & black_mask(g2)
    e1, e2 = enhance(g1), enhance(g2)
    p1, p2 = match_points(e1, e2, m1, m2)
    diag = {"第三人稱": tp, "matches": int(len(p1)), "static": 0, "moving_inliers": 0,
            "iqr_yaw": None, "iqr_pitch": None, "total_rot": None, "pre_turn": 0}
    if len(p1) == 0:
        return None, None, 0, 0.0, diag

    disp = np.linalg.norm(p2 - p1, axis=1)
    static = disp < STATIC_PX
    n_static = int(static.sum())
    diag["static"] = n_static
    best = None   # (conf, n, yaw, pitch, iqr_y, iqr_p, total, pre)
    cands = []
    r = _fit_rotation(p1[~static], p2[~static], W, H)
    if r is not None:
        yaw, pitch, n, conf, iqr_y, iqr_p, total = r
        best = (conf, n, yaw, pitch, iqr_y, iqr_p, total, 0)
        cands.append(best)

    # 直接比對不夠可信 → 預轉再比（轉很大時 ORB 在拉伸的邊緣認不出同一個點）
    swept = False
    if sweep and (best is None or best[0] < OK_CONF):
        swept = True
        sp = p1[static]
        m1s = _punch_static(m1, sp) if n_static else m1
        m2s = _punch_static(m2, p2[static]) if n_static else m2
        for pre in PRE_TURNS:
            Hm = _rot_H(W, H, pre)
            w1 = cv2.warpPerspective(g1, Hm, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
            wm = cv2.warpPerspective(m1s, Hm, (W, H), flags=cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
            wm &= black_mask(w1)
            q1, q2 = match_points(enhance(w1), e2, wm, m2s)
            r = _fit_rotation(q1, q2, W, H)
            if r is None:
                continue
            yaw, pitch, n, conf, iqr_y, iqr_p, total = r
            cand = (conf, n, yaw + pre, pitch, iqr_y, iqr_p, total, pre)
            cands.append(cand)
            if best is None or (cand[0], cand[1]) > (best[0], best[1]):
                best = cand

    if best is None or best[0] <= 0.0:
        # 動的點太少：靜止點很多就是根本沒轉
        if n_static >= 20:
            return 0.0, 0.0, n_static, _conf(n_static, 0.0), diag
        return None, None, 0 if best is None else best[1], 0.0, diag

    # 光度驗證：每個候選答案都扭過去疊疊看，挑疊得最好的（假點群湊出的答案疊不上）；疊得差的再把可信度打折
    scored = []
    for c in cands:
        if c[0] <= 0:
            continue
        v = _verify_ncc(e1, e2, c[2], c[3], m1, m2)
        scored.append((-1.0 if v is None else v, c))
    if scored:
        scored.sort(key=lambda t: (round(t[0], 2), t[1][0], t[1][1]), reverse=True)
        ncc, best = scored[0]
        diag["ncc"] = round(ncc, 3)
    else:
        ncc = None
    conf, n, yaw, pitch, iqr_y, iqr_p, total, pre = best
    if ncc is not None:
        # ncc ≥0.4 不扣；0.25 → 一半；≤0.19 → 三折。但點很多又一致（≥80 點）就不靠光度：貼著牆的粗糙紋理 NCC 本來就低
        f_ncc = max(0.3, min(1.0, (ncc - 0.1) / 0.3))
        w = max(0.0, min(1.0, (80 - n) / 60))
        conf = round(conf * (1 - (1 - f_ncc) * w), 3)
    if swept:
        others = [c for c in cands if c is not best and c[0] > 0.2 and abs(c[2] - yaw) < 3 and abs(c[3] - pitch) < 3]
        diag["corroborated"] = len(others)
    diag.update({"moving_inliers": n, "iqr_yaw": iqr_y, "iqr_pitch": iqr_p, "total_rot": total, "pre_turn": pre})
    # 靜止點遠多於動的點、而且算出來幾乎沒轉 → 當作沒轉
    if n_static >= 3 * n and n_static >= 30 and abs(yaw) < 3 and abs(pitch) < 3:
        return 0.0, 0.0, n_static, _conf(n_static, 0.0), diag
    return round(yaw, 2), round(pitch, 2), n, conf, diag


# ---------- 2. 放大倍數 ----------
def _ncc_zoom(g1: np.ndarray, g2: np.ndarray, tmask: np.ndarray, tp: bool = False):
    """多尺度 NCC（走路主要靠這個）：before 中央塊放大成各種倍數，在 after 中央附近找最像的。
    第一人稱用小塊（x/y 0.38～0.62，就是工具說的「中央那個東西」）；第三人稱中央是自己，改用 0.3～0.7 扣掉角色的環。
    真實走路有視差（遠近東西放大倍數不同），NCC 通常只有 0.5～0.75；合成資料 0.98。"""
    H, W = g1.shape
    b, win = ((0.3, 0.7), 0.08) if tp else ((0.38, 0.62), 0.12)
    cx0, cx1, cy0, cy1 = int(b[0] * W), int(b[1] * W), int(b[0] * H), int(b[1] * H)
    tpl = g1[cy0:cy1, cx0:cx1]
    tm = tmask[cy0:cy1, cx0:cx1]
    best, best_z = -1.0, None
    for z in np.arange(0.8, 2.21, 0.02):
        tw, th = int(tpl.shape[1] * z), int(tpl.shape[0] * z)
        if tw < 8 or th < 8:
            continue
        # 放大後的樣板應該出現在 after 的中央附近（±win 畫面）：只在那個窗找
        ex, ey = W / 2 - tw / 2, H / 2 - th / 2
        sx0, sy0 = int(max(0, ex - win * W)), int(max(0, ey - win * H))
        sx1, sy1 = int(min(W, ex + tw + win * W)), int(min(H, ey + th + win * H))
        crop = g2[sy0:sy1, sx0:sx1]
        if crop.shape[0] < th or crop.shape[1] < tw:
            continue
        t2 = cv2.resize(tpl, (tw, th), interpolation=cv2.INTER_AREA if z < 1 else cv2.INTER_LINEAR)
        m2 = cv2.resize(tm, (tw, th), interpolation=cv2.INTER_NEAREST)
        try:
            r = cv2.matchTemplate(crop, t2, cv2.TM_CCOEFF_NORMED, mask=m2)
        except cv2.error:
            r = cv2.matchTemplate(crop, t2, cv2.TM_CCOEFF_NORMED)
        v = float(np.nan_to_num(r).max())
        if v > best:
            best, best_z = v, float(z)
    if best_z is None or best < 0.4:
        return None, 0.0, {"ncc": round(best, 3), "method": "ncc"}
    conf = max(0.0, min(1.0, (best - 0.35) / 0.4))        # ncc 0.55 → 0.5；0.75 → 1.0
    if best_z >= 2.15 or best_z <= 0.85:                   # 撞到搜尋邊界＝不可靠
        conf *= 0.5
    return round(best_z, 3), round(conf, 3), {"ncc": round(best, 3), "method": "ncc"}


def estimate_zoom(before, after, third_person=None):
    """
    前後兩張圖 → (zoom, 可信度, 診斷dict)。zoom >1 ＝ after 的中央比 before 放大（靠近了）。
    中央區特徵點對的相對距離比取中位數（RANSAC 相似轉換挑內點）；特徵不夠退回多尺度 NCC。
    """
    g1, g2 = load_gray(before), load_gray(after)
    if g1.shape != g2.shape:
        g2 = cv2.resize(g2, (g1.shape[1], g1.shape[0]))
    H, W = g1.shape
    tp = _is_third_person(g1, g2, third_person)
    diag = {"第三人稱": tp, "derot": None}
    r = _zoom_features(g1, g2, tp)
    # 走路常順便轉頭：相似轉換只會算純放大，混著轉會量錯 → 把 before 照估出的轉角反轉回去再量一次，兩個取比較一致的
    y0, p0, n0, c0, _ = estimate_yaw_pitch(g1, g2, tp, sweep=False)
    if y0 is not None and c0 >= 0.3 and (abs(y0) > 2 or abs(p0) > 2):
        g1r = cv2.warpPerspective(g1, _rot_H(W, H, y0, p0), (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
        rr = _zoom_features(g1r, g2, tp)
        if rr is not None and (r is None or rr[1] > r[1]):
            r = rr
            diag["derot"] = (round(y0, 1), round(p0, 1))
    if r is not None and r[1] >= ZOOM_OK_CONF:
        z, c, d = r
        diag.update(d)
        return z, c, diag
    # 特徵點版不夠可信（真實走路有視差、相似轉換湊不出來）→ 多尺度 NCC，兩個取可信度高的
    z, c, d2_ = _ncc_zoom(enhance(g1), enhance(g2), make_mask(W, H, tp), tp)
    if r is not None and r[1] > c:
        z, c, d2_ = r
    diag.update(d2_)
    return z, c, diag


def _zoom_features(g1: np.ndarray, g2: np.ndarray, tp: bool):
    """特徵點版放大估計 → (zoom, conf, diag) 或 None。"""
    H, W = g1.shape
    m1 = make_mask(W, H, tp, box=ZOOM_BOX) & black_mask(g1)
    m2 = make_mask(W, H, tp, box=ZOOM_BOX) & black_mask(g2)
    p1, p2 = match_points(enhance(g1), enhance(g2), m1, m2)
    diag = {"matches": int(len(p1)), "inliers": 0, "static": 0}
    if len(p1):
        # 完全沒動的點（HUD、角色、提示框）不混進放大估計；全部都沒動＝沒靠近
        static = np.linalg.norm(p2 - p1, axis=1) < STATIC_PX
        diag["static"] = int(static.sum())
        if (~static).sum() < 6 and static.sum() >= 20:
            diag["method"] = "static"
            return 1.0, 0.8, diag
        p1, p2 = p1[~static], p2[~static]
    if len(p1) < 6:
        return None
    A, msk = cv2.estimateAffinePartial2D(p1, p2, method=cv2.RANSAC, ransacReprojThreshold=3.0, maxIters=3000)
    if A is None or msk is None or msk.sum() < 6:
        return None
    inl = msk.ravel().astype(bool)
    q1, q2 = p1[inl], p2[inl]
    n = int(len(q1))
    diag["inliers"] = n
    spread1 = float(np.std(q1, axis=0).mean())
    spread2 = float(np.std(q2, axis=0).mean())
    if spread1 <= 15 or spread2 <= 15:   # 擋掉「全部對到同一點」的退化解（重複花紋／重複圖示）
        return None
    rng = np.random.default_rng(0)
    idx = rng.integers(0, n, size=(min(600, n * (n - 1) // 2), 2))
    idx = idx[idx[:, 0] != idx[:, 1]]
    d1 = np.linalg.norm(q1[idx[:, 0]] - q1[idx[:, 1]], axis=1)
    d2 = np.linalg.norm(q2[idx[:, 0]] - q2[idx[:, 1]], axis=1)
    ok = (d1 > 12) & (d2 > 4)
    if ok.sum() < 5:
        return None
    ratios = d2[ok] / d1[ok]
    z = float(np.median(ratios))
    iqr = float(np.subtract(*np.percentile(ratios, [75, 25])))
    diag.update({"iqr": round(iqr, 3), "affine_scale": round(float(math.hypot(A[0, 0], A[1, 0])), 3), "method": "features"})
    conf = max(0.0, 1.0 - iqr / 0.3) * min(1.0, n / 25)
    if not (0.5 <= z <= 2.5):
        return None
    return round(z, 3), round(conf, 3), diag


# ---------- 3. 合成資料 ----------
def synth_rotate(gray: np.ndarray, yaw_deg: float, pitch_deg: float = 0.0, jpeg: bool = True) -> np.ndarray:
    """合成純旋轉：p_after = K·R·K⁻¹·p_before（真正的針孔旋轉，不只是逐欄重取樣）。yaw 正＝右轉；pitch 正＝抬頭。黑邊留著。"""
    H, W = gray.shape
    out = cv2.warpPerspective(gray, _rot_H(W, H, yaw_deg, pitch_deg), (W, H), flags=cv2.INTER_LINEAR,
                              borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    return _jpeg(out) if jpeg else out


def synth_zoom(gray: np.ndarray, z: float, jpeg: bool = True) -> np.ndarray:
    """中央裁切再放大 z 倍。"""
    H, W = gray.shape
    cw, ch = W / z, H / z
    x0, y0 = (W - cw) / 2, (H - ch) / 2
    M = np.array([[z, 0, -x0 * z], [0, z, -y0 * z]], dtype=np.float64)
    out = cv2.warpAffine(gray, M, (W, H), flags=cv2.INTER_LINEAR)
    return _jpeg(out) if jpeg else out


def synth_hud(before: np.ndarray, after: np.ndarray) -> np.ndarray:
    """模擬真實第三人稱：把 before 的 HUD 邊緣（上下左右）跟中央角色區原封不動貼回 after（這些東西釘在畫面上不跟著轉）。"""
    H, W = before.shape
    out = after.copy()
    t, b, l, r = HUD_MARGIN
    out[:int(t * H), :] = before[:int(t * H), :]
    out[H - int(b * H):, :] = before[H - int(b * H):, :]
    out[:, :int(l * W)] = before[:, :int(l * W)]
    out[:, W - int(r * W):] = before[:, W - int(r * W):]
    x0, x1, y0, y1 = SELF_BOX
    # 角色本體比遮罩框小一點（x 0.43～0.57、y 0.40～0.90），讓遮罩邊緣有一點「沒遮到的靜止東西」考驗靜止點處理
    out[int(0.40 * H):int(0.90 * H), int(0.43 * W):int(0.57 * W)] = before[int(0.40 * H):int(0.90 * H), int(0.43 * W):int(0.57 * W)]
    return out


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
    print(f"（合成旋轉是真正的針孔旋轉 K·R·K⁻¹ 再過一次 JPEG q80；可信度 <{OK_CONF} 算「量不出」，跟 --report 同一標準）")
    yaws = [-45, -30, -20, -10, -5, 5, 10, 20, 30, 45]
    pitches = [-10, -5, 5, 10]
    zooms = [1.1, 1.3, 1.6]
    rows_yaw, rows_pitch, rows_zoom = defaultdict(list), defaultdict(list), defaultdict(list)
    rows_yaw3, rows_zoom3 = defaultdict(list), defaultdict(list)
    fail = defaultdict(int)
    for f in files:
        g = load_gray(f)
        for th in yaws:
            y, p, n, c, d = estimate_yaw_pitch(g, synth_rotate(g, th))
            if y is None or c < OK_CONF:
                fail[("yaw", th)] += 1
                continue
            rows_yaw[th].append((abs(y - th), c, n, abs(p)))
        for th in yaws:
            a = _jpeg(synth_hud(g, synth_rotate(g, th, jpeg=False)))
            y, p, n, c, d = estimate_yaw_pitch(g, a)
            if y is None or c < OK_CONF:
                fail[("yaw3", th)] += 1
                continue
            rows_yaw3[th].append((abs(y - th), c, n, abs(p)))
        for z in zooms:
            a = _jpeg(synth_hud(g, synth_zoom(g, z, jpeg=False)))
            ze, c, d = estimate_zoom(g, a)
            if ze is None or c < ZOOM_OK_CONF:
                fail[("zoom3", z)] += 1
                continue
            rows_zoom3[z].append((abs(ze - z), c, d.get("inliers", 0)))
        for ph in pitches:
            y, p, n, c, d = estimate_yaw_pitch(g, synth_rotate(g, 0.0, ph))
            if p is None or c < OK_CONF:
                fail[("pitch", ph)] += 1
                continue
            rows_pitch[ph].append((abs(p - ph), c, n, abs(y)))
        for z in zooms:
            ze, c, d = estimate_zoom(g, synth_zoom(g, z))
            if ze is None or c < ZOOM_OK_CONF:
                fail[("zoom", z)] += 1
                continue
            rows_zoom[z].append((abs(ze - z), c, d.get("inliers", 0)))

    def _tab(title, kind, rows, unit, key_fmt):
        print(f"\n{title}")
        print("| 真值 | 量到筆數 | 中位誤差 | 最大誤差 | 平均可信度 | 平均點數 | 另一軸誤差中位 | 量不出 |")
        print("|---|---|---|---|---|---|---|---|")
        all_err = []
        keys = sorted(set(rows) | {k for (kk, k) in fail if kk == kind})
        for k in keys:
            r = rows.get(k, [])
            errs = [x[0] for x in r]
            all_err += errs
            nf = fail.get((kind, k), 0)
            if not r:
                print(f"| {key_fmt(k)} | 0 | - | - | - | - | - | {nf} |")
                continue
            other = f"{np.median([x[3] for x in r]):.1f}" if len(r[0]) > 3 else "-"
            print(f"| {key_fmt(k)} | {len(r)} | {np.median(errs):.2f}{unit} | {max(errs):.2f}{unit} | {np.mean([x[1] for x in r]):.2f} | {np.mean([x[2] for x in r]):.0f} | {other} | {nf} |")
        if all_err:
            print(f"全部：中位 {np.median(all_err):.2f}{unit}、90 百分位 {np.percentile(all_err, 90):.2f}{unit}、最大 {max(all_err):.2f}{unit}")
        return all_err

    e_y = _tab("合成純旋轉（yaw）", "yaw", rows_yaw, "°", lambda k: f"{k:+d}°")
    e_p = _tab("合成抬頭低頭（pitch）", "pitch", rows_pitch, "°", lambda k: f"{k:+d}°")
    e_z = _tab("合成中央放大（zoom）", "zoom", rows_zoom, "", lambda k: f"×{k}")
    e_y3 = _tab("合成純旋轉＋HUD 與中央角色釘住不動（模擬真實第三人稱）", "yaw3", rows_yaw3, "°", lambda k: f"{k:+d}°")
    e_z3 = _tab("合成中央放大＋HUD 與中央角色釘住不動", "zoom3", rows_zoom3, "", lambda k: f"×{k}")
    tot_fail = sum(fail.values())
    tot = len(files) * (2 * len(yaws) + len(pitches) + 2 * len(zooms))
    print(f"\n量不出的次數：{tot_fail}／{tot}" + (f"（{ {f'{a}{b}': c for (a, b), c in fail.items()} }）" if tot_fail else ""))
    ok_y = bool(e_y) and bool(e_y3) and np.median(e_y) < 2 and np.median(e_y3) < 2
    ok_z = bool(e_z) and bool(e_z3) and np.median(e_z) < 0.1 and np.median(e_z3) < 0.1
    print(f"目標：轉角中位誤差 <2° → {'達標' if ok_y else '未達標'}；放大中位誤差 <0.1 → {'達標' if ok_z else '未達標'}"
          + (f"（放大最大誤差 {max(e_z + e_z3):.2f}）" if e_z and e_z3 else ""))
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


def _fmt(v, nd=0):
    return "無" if v is None else (f"{v:+.{nd}f}" if isinstance(v, (int, float)) else str(v))


def report(limit=None, third_person=None):
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
    results, missing = [], 0
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
            y, p, n, c, d = estimate_yaw_pitch(b, a, third_person, sweep=(tool != "走"))
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

    L = [f"# 獨立驗證報告（{len(results)} 筆，{missing} 筆缺圖）\n",
         "方法：ORB 特徵點＋RANSAC＋針孔模型，跟代理工具無關（見 工具版/獨立驗證.py 頂端）。",
         f"「真值」＝本程式估的；「量得到」＝可信度 ≥ {OK_CONF}；轉角正＝右轉、抬頭正＝往上。宣稱與真值的差都取絕對值。\n"]
    worst = []   # (差, tag, 工具, 宣稱文字, 真值文字)

    def _measurable(o):
        return o.get("真yaw") is not None and (o.get("可信") or 0) >= OK_CONF

    def _zoom_ok(o):
        return o.get("真放大") is not None and (o.get("放大可信") or 0) >= ZOOM_OK_CONF

    def _truth_txt(o):
        return f"yaw {o['真yaw']:+.1f}°（pitch {o['真pitch']:+.1f}°，{o['n']} 點，可信 {o['可信']:.2f}）"

    for tool in ("轉", "轉段", "視角移動到", "走"):
        rs = by_tool.get(tool, [])
        L.append(f"## {tool}（{len(rs)} 筆）\n")
        if not rs:
            L.append("沒有資料。\n")
            continue
        meas = [o for o in rs if (_zoom_ok(o) if tool == "走" else _measurable(o))]
        L.append(f"真值量得到：{len(meas)}／{len(rs)}（{100 * len(meas) / len(rs):.0f}%）\n")
        if tool in ("轉", "轉段"):
            cols = ["送出度", "標點度", "整張度"] + (["用的"] if tool == "轉段" else [])
            L.append("| 宣稱欄位 | 有值且真值量得到 | 跟真值差 中位 | 90 百分位 | 差 >5° 筆數 | 宣稱「量不出」筆數 | 其中真值其實量得到 |")
            L.append("|---|---|---|---|---|---|---|")
            for col in cols:
                diffs, big, none_n, none_meas = [], 0, 0, 0
                for o in rs:
                    v = _f(o.get(col))
                    if v is None:
                        none_n += 1
                        none_meas += _measurable(o)
                        continue
                    if _measurable(o):
                        dd = v - o["真yaw"]
                        diffs.append(dd)
                        big += abs(dd) > 5
                med, p90, n = _stats(diffs)
                pct = f"{none_meas}／{none_n}（{100 * none_meas / none_n:.0f}%）" if none_n else "0／0"
                L.append(f"| {col} | {n} | {med} | {p90} | {big} | {none_n} | {pct} |")
            claim_ok = claim_ok_bad = 0
            for o in rs:
                want, lm = _f(o.get("送出度")), _f(o.get("標點度"))
                if want is None or lm is None:
                    continue
                if abs(lm - want) <= max(5.0, 0.15 * abs(want)):
                    claim_ok += 1
                    claim_ok_bad += _measurable(o) and abs(o["真yaw"] - want) > 5
            L.append(f"\n工具宣稱「標點跟送出一致」的 {claim_ok} 筆裡，真值跟送出差 >5° 的：{claim_ok_bad} 筆。")
            ms = [o for o in meas if _f(o.get("送出度")) is not None]
            diffs = [_f(o.get("送出度")) - o["真yaw"] for o in ms]
            med, p90, n = _stats(diffs)
            over = sum(1 for o in ms if abs(o["真yaw"]) > abs(_f(o.get("送出度"))) + 5)
            under = sum(1 for o in ms if abs(o["真yaw"]) < abs(_f(o.get("送出度"))) - 5)
            rev = sum(1 for o in ms if _f(o.get("送出度")) and o["真yaw"] * _f(o.get("送出度")) < 0 and abs(o["真yaw"]) > 3)
            L.append(f"送出度 vs 真值（{n} 筆）：差中位 {med}°、90 百分位 {p90}°；實際轉過頭（|真|>|送出|+5）{over} 筆、轉不夠（|真|<|送出|−5）{under} 筆、方向相反 {rev} 筆。\n")
            for o in meas:
                used = _f(o.get("用的")) if tool == "轉段" else None
                claim = used if used is not None else (_f(o.get("標點度")) if _f(o.get("標點度")) is not None else _f(o.get("送出度")))
                if claim is not None:
                    worst.append((abs(claim - o["真yaw"]), o.get("檔"), tool,
                                  f"送出 {_fmt(_f(o.get('送出度')))}／標點 {_fmt(_f(o.get('標點度')))}／整張 {_fmt(_f(o.get('整張度')))}"
                                  + (f"／用的 {_fmt(used)}" if tool == "轉段" else ""), _truth_txt(o)))
        elif tool == "視角移動到":
            L.append("| 宣稱欄位 | 有值且真值量得到 | 跟真值差 中位 | 90 百分位 | 差 >5° 筆數 |")
            L.append("|---|---|---|---|---|")
            for col, key in (("送出度", "真yaw"), ("送出上下", "真pitch")):
                diffs = [_f(o.get(col)) - o[key] for o in meas if _f(o.get(col)) is not None]
                med, p90, n = _stats(diffs)
                L.append(f"| {col} | {n} | {med} | {p90} | {sum(1 for d in diffs if abs(d) > 5)} |")
            L.append("")
            for o in meas:
                v = _f(o.get("送出度"))
                if v is not None:
                    worst.append((abs(v - o["真yaw"]), o.get("檔"), tool, f"送出 {v:+.0f}／上下 {_fmt(_f(o.get('送出上下')))}", _truth_txt(o)))
        elif tool == "走":
            diffs, none_n, none_meas, big = [], 0, 0, 0
            for o in rs:
                v = _f(o.get("放大"))
                if v is None:
                    none_n += 1
                    none_meas += _zoom_ok(o)
                    continue
                if _zoom_ok(o):
                    diffs.append(v - o["真放大"])
                    big += abs(v - o["真放大"]) > 0.2
                    worst.append((abs(v - o["真放大"]), o.get("檔"), tool, f"放大 {v:.2f}（走 {o.get('秒')} 秒）",
                                  f"放大 {o['真放大']:.2f}（可信 {o['放大可信']:.2f}，{o.get('放大診', {}).get('method', '?')}）"))
            med, p90, n = _stats(diffs)
            pct = f"{none_meas}／{none_n}（{100 * none_meas / none_n:.0f}%）" if none_n else "0／0"
            L.append("| 宣稱欄位 | 有值且真值量得到 | 跟真值差 中位 | 90 百分位 | 差 >0.2 筆數 | 宣稱「量不出」筆數 | 其中真值其實量得到 |")
            L.append("|---|---|---|---|---|---|---|")
            L.append(f"| 放大 | {n} | {med} | {p90} | {big} | {none_n} | {pct} |")
            ys = [o for o in rs if _measurable(o)]
            if ys:
                L.append(f"\n走路時鏡頭有沒有順便轉（僅參考：走路有視差、不是純旋轉）：|真 yaw| >5° 的 {sum(1 for o in ys if abs(o['真yaw']) > 5)}／{len(ys)} 筆。")
            L.append("")

    worst.sort(key=lambda t: -t[0])
    L += ["## 最離譜的 10 筆（宣稱 vs 真值）\n", "| 差 | tag | 工具 | 宣稱 | 真值 |", "|---|---|---|---|---|"]
    for d, tag, tool, c, t in worst[:10]:
        L.append(f"| {d:.1f} | {tag} | {tool} | {c} | {t} |")
    L += ["", "## 每筆明細\n", "| tag | 工具 | 宣稱 | 真 yaw | 真 pitch | 點數 | 可信 | 預轉 | 真放大 |", "|---|---|---|---|---|---|---|---|---|"]
    for o in results:
        tool = o.get("工具")
        if tool in ("轉", "轉段"):
            c = f"送出 {_fmt(_f(o.get('送出度')))}／標點 {_fmt(_f(o.get('標點度')))}／整張 {_fmt(_f(o.get('整張度')))}" + (f"／用的 {_fmt(_f(o.get('用的')))}" if tool == "轉段" else "")
        elif tool == "視角移動到":
            c = f"送出 {_fmt(_f(o.get('送出度')))}／上下 {_fmt(_f(o.get('送出上下')))}"
        else:
            c = f"放大 {o.get('放大')}／秒 {o.get('秒')}"
        yv = "量不出" if o.get("真yaw") is None else f"{o['真yaw']:+.1f}"
        pv = "-" if o.get("真pitch") is None else f"{o['真pitch']:+.1f}"
        zv = "" if tool != "走" else ("量不出" if o.get("真放大") is None else f"{o['真放大']:.2f}（可信 {o['放大可信']:.2f}）")
        L.append(f"| {o.get('檔')} | {tool} | {c} | {yv} | {pv} | {o.get('n')} | {o.get('可信'):.2f} | {o.get('診', {}).get('pre_turn', 0)} | {zv} |")
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
    ap.add_argument("--first-person", action="store_true", help="強制不排除中央角色區")
    ap.add_argument("--third-person", action="store_true", help="強制排除中央角色區")
    ap.add_argument("--n-img", type=int, default=10, help="--selftest 用幾張截圖")
    args = ap.parse_args()
    USE_AKAZE = args.akaze
    tp = True if args.third_person else (False if args.first_person else None)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    if args.selftest:
        return selftest(args.n_img)
    if args.report:
        return report(args.limit, tp)
    if args.pair:
        y, p, n, c, d = estimate_yaw_pitch(args.pair[0], args.pair[1], tp)
        z, zc, zd = estimate_zoom(args.pair[0], args.pair[1], tp)
        print(f"yaw {y}°（正＝右轉） pitch {p}°（正＝抬頭） 點數 {n} 可信 {c} 診斷 {d}")
        print(f"放大 {z} 可信 {zc} 診斷 {zd}")
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
