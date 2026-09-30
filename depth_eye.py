# -*- coding: utf-8 -*-
"""
depth_eye.py — 視覺深度：從一張遊戲畫面估出「每個方向的牆有多遠」。（2026-09-30 使用者要求）

本機跑 Depth Anything V2（Metric-Indoor-Small），一張圖約幾十毫秒，不呼叫 DGX 上的大腦。
輸出的是「公尺」，但遊戲畫面不是真實相機，所以這個公尺只當成**相對刻度**：
換算成「走幾秒」的比例由程式自己量（往前走已知秒數，看正前方深度少了多少）——見 GridMap.calibrate。

  python depth_eye.py 某張截圖.jpg        # 印出各方向的深度，存一張深度圖
"""
import os
import sys

MODEL_ID = os.environ.get("GAMER_DEPTH_MODEL", "depth-anything/Depth-Anything-V2-Metric-Indoor-Small-hf")
HFOV_DEG = 70.0          # 跟 flow_agent 一樣：整個畫面寬約 70 度


class DepthEye:
    def __init__(self, device=None):
        import torch
        from transformers import AutoImageProcessor, AutoModelForDepthEstimation
        self.torch = torch
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.proc = AutoImageProcessor.from_pretrained(MODEL_ID)
        self.model = AutoModelForDepthEstimation.from_pretrained(MODEL_ID).to(self.device).eval()

    def depth(self, im):
        """PIL 圖 → 跟圖一樣大小的深度陣列（numpy float32，越大越遠）。"""
        import numpy as np
        torch = self.torch
        im = im.convert("RGB")
        inputs = self.proc(images=im, return_tensors="pt").to(self.device)
        with torch.no_grad():
            out = self.model(**inputs).predicted_depth
        d = torch.nn.functional.interpolate(out.unsqueeze(1), size=(im.height, im.width),
                                            mode="bicubic", align_corners=False)[0, 0]
        return d.float().cpu().numpy().astype(np.float32)

    def rays(self, im, n=9, band=(0.40, 0.56), skip=None, self_box=None):
        """把畫面橫切成 n 條視線，回傳每條的 (偏離正前方的角度, 沿鏡頭軸的深度)。

        band：取畫面哪一段高度（0～1）。鏡頭水平時，視線高度那一帶打到的就是牆或擋路的東西。
        skip：[(x0, x1), …] 要略過的橫向範圍（0～1），例如 YOLO 框到的人——人會走，不能畫成牆。
        self_box：(x0, y0, x1, y1) 自己的背影（第三人稱）。那一塊的深度＝鏡頭到自己的距離，
                  回傳時放在 'self' 裡，地圖用它把「鏡頭量到的距離」換成「從自己算起的距離」。
        """
        import math
        import numpy as np
        d = self.depth(im)
        h, w = d.shape
        y0, y1 = int(h * band[0]), max(int(h * band[0]) + 1, int(h * band[1]))
        strip = d[y0:y1, :]
        out = []
        for i in range(n):
            x0, x1 = int(w * i / n), int(w * (i + 1) / n)
            cx = (i + 0.5) / n
            if any(a <= cx <= b for a, b in (skip or [])):
                continue
            if self_box and self_box[0] <= cx <= self_box[2] and self_box[1] < band[1]:
                continue                                  # 這條視線被自己的身體擋住
            z = float(np.median(strip[:, x0:x1]))
            ang = math.degrees(math.atan((cx - 0.5) * 2 * math.tan(math.radians(HFOV_DEG / 2))))
            out.append((ang, z))
        me = None
        if not self_box:
            # 沒有 YOLO 的自己框時，用畫面判斷有沒有「自己的背影」擋在正中間（第三人稱）：
            # 正中間偏下那一塊如果明顯比左右同高度的地方近，那就是身體；它的深度＝鏡頭到自己的距離。
            # 第一人稱時那一塊是地板，跟左右差不多遠，不會被當成身體。
            c = float(np.median(d[int(h * 0.50):int(h * 0.62), int(w * 0.46):int(w * 0.54)]))
            l = float(np.median(d[int(h * 0.50):int(h * 0.62), int(w * 0.28):int(w * 0.36)]))
            r = float(np.median(d[int(h * 0.50):int(h * 0.62), int(w * 0.64):int(w * 0.72)]))
            if c < 0.75 * min(l, r):
                me = c
        if self_box:
            bx0, by0, bx1, by1 = (int(self_box[0] * w), int(self_box[1] * h), int(self_box[2] * w), int(self_box[3] * h))
            if bx1 > bx0 and by1 > by0:
                me = float(np.median(d[by0:by1, bx0:bx1]))
        return {"rays": out, "self": me, "depth": d}


def main():
    from PIL import Image
    import numpy as np
    p = sys.argv[1]
    eye = DepthEye()
    im = Image.open(p)
    import time
    t0 = time.time()
    r = eye.rays(im)
    print(f"{os.path.basename(p)} {im.size}　{time.time() - t0:.2f}s")
    print("  " + "  ".join(f"{a:+.0f}°:{z:.1f}" for a, z in r["rays"]))
    d = r["depth"]
    g = (255 * (1 - (d - d.min()) / max(1e-6, d.max() - d.min()))).astype(np.uint8)
    out = os.path.splitext(p)[0] + "_depth.png"
    if len(sys.argv) > 2:
        out = sys.argv[2]
    Image.fromarray(g).save(out)
    print("  深度圖：", out)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
