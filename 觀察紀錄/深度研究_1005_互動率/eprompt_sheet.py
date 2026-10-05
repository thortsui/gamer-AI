# 把找 E 提示的判斷跑在某專案全部存下來的截圖上，統計命中率，並把命中的位置剪下來拼成一張圖給人眼核對（只讀）
import sys, io, os, glob, random
import cv2
import numpy as np
sys.path.insert(0, os.path.dirname(__file__))
from eprompt_test import e_hits

game, out = sys.argv[1], sys.argv[2]
pat = sys.argv[3] if len(sys.argv) > 3 else "*_定時.jpg"
maxn = int(sys.argv[4]) if len(sys.argv) > 4 else 400
d = os.path.join(r"D:\Documents\ThorWorkSpace\gamer AI\專案", game, "記憶", "畫面")
files = sorted(glob.glob(os.path.join(d, pat)))
random.seed(7)
if len(files) > maxn:
    files = sorted(random.sample(files, maxn))
tiles = []; nhit = 0
for f in files:
    hs = e_hits(f)
    if not hs:
        continue
    nhit += 1
    img = cv2.imdecode(np.fromfile(f, dtype=np.uint8), cv2.IMREAD_COLOR)
    H, W = img.shape[:2]
    for (cx, cy) in hs[:1]:
        x, y = int(cx * W), int(cy * H)
        x0, y0 = max(0, min(W - 220, x - 110)), max(0, min(H - 150, y - 75))
        crop = img[y0:y0 + 150, x0:x0 + 220].copy()
        cv2.circle(crop, (x - x0, y - y0), 16, (0, 0, 255), 1)
        cv2.putText(crop, os.path.basename(f)[:6], (3, 12), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 255), 1)
        tiles.append(crop)
print(f"{game}：{len(files)} 張截圖（{pat}），判定「有 E 提示」的 {nhit} 張（{100*nhit/max(len(files),1):.0f}%）")
tiles = tiles[:48]
if tiles:
    cols = 6
    rows = (len(tiles) + cols - 1) // cols
    sheet = np.zeros((rows * 150, cols * 220, 3), np.uint8)
    for i, t in enumerate(tiles):
        r, c = divmod(i, cols)
        sheet[r * 150:r * 150 + t.shape[0], c * 220:c * 220 + t.shape[1]] = t
    cv2.imencode(".jpg", sheet, [cv2.IMWRITE_JPEG_QUALITY, 80])[1].tofile(out)
    print("拼圖：", out, f"（{len(tiles)} 格）")
