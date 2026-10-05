# 把 flow_agent.e_prompt() 的判斷原封不動搬出來，離線跑在存下來的截圖上（只讀，不碰遊戲）
import sys, io, os, glob
import cv2
import numpy as np
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


def e_hits(path, width=640):
    data = np.fromfile(path, dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        return None
    h0, w0 = img.shape[:2]
    img = cv2.resize(img, (width, int(h0 * width / w0)), interpolation=cv2.INTER_AREA)
    h, w = img.shape[:2]
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    s, v = hsv[:, :, 1], hsv[:, :, 2]
    white = ((s < 60) & (v > 170)).astype(np.uint8) * 255
    white = cv2.morphologyEx(white, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    cnts, _ = cv2.findContours(white, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = []
    for c in cnts:
        x, y, bw, bh = cv2.boundingRect(c)
        if not (w * 0.012 <= bw <= w * 0.06) or not (h * 0.02 <= bh <= h * 0.09):
            continue
        if not (0.7 <= bw / max(1, bh) <= 1.5):
            continue
        if cv2.contourArea(c) / max(1, bw * bh) < 0.55:
            continue
        cx, cy = (x + bw / 2) / w, (y + bh / 2) / h
        if not (0.12 <= cy <= 0.85):
            continue
        if cx < 0.18 and cy < 0.20:
            continue
        out.append((round(cx, 3), round(cy, 3)))
    return out[:3]


if __name__ == "__main__":
    for p in sys.argv[1:]:
        for f in sorted(glob.glob(p)):
            print(os.path.basename(f), "→", e_hits(f))
