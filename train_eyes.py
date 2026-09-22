"""train_eyes.py — 讓他自己訓練自己的眼睛（2026-09-15）

流程（全部離線，不需要模型在線）：
  1. 把 專案/<遊戲>/素材/標註 裡的畫面＋YOLO 框收成資料集
  2. 套用他自己的更正（記憶/眼睛更正.jsonl）：
       - 「yolo 說是 A，其實是 B」 → 那個框的類別改成 B
       - 「YOLO 沒框到 C」      → 用 YOLO-World 拿 C 這個詞回頭在那張畫面上補框
  3. 微調 YOLO-World（保留開放詞彙，學會這個遊戲的長相）
  4. 存成 weights/eyes_trained.pt；下次開機 local_vision 自動用它

用法：
  python train_eyes.py --game 動物醫院            # 完整訓練（收工時自動跑）
  python train_eyes.py --game 動物醫院 --smoke    # 冒煙測試：60 張、1 個 epoch
"""
import argparse, io, json, os, random, shutil, sys, time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
MAX_IMAGES = 1500
MAX_CLASSES = 60           # 類別超過就不訓練（垃圾詞會毀掉權重）          # 完整訓練最多拿最近這麼多張（4060 筆電：3 epochs 約 40 分鐘）


def log(msg):
    print(msg, flush=True)


def load_jsonl(path):
    out = []
    if os.path.exists(path):
        for line in io.open(path, encoding="utf-8", errors="replace"):
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except Exception:  # noqa: BLE001
                    pass
    return out


def build_dataset(game, smoke=False):
    base = os.path.join(HERE, "專案", game, "素材", "標註")
    mem = os.path.join(HERE, "專案", game, "記憶")
    classes = json.load(io.open(os.path.join(base, "類別表.json"), encoding="utf-8"))
    corr = load_jsonl(os.path.join(mem, "眼睛更正.jsonl"))
    rename = {}                        # yolo 名 → 他說其實是
    new_words = set()                  # 他說 YOLO 沒框到的東西
    for c in corr:
        for r in c.get("yolo更正") or []:
            a, b = str(r.get("yolo", "")).strip().lower(), str(r.get("其實是", "")).strip().lower()
            if a and b and a != b:
                rename[a] = b
        for w in c.get("沒框到") or []:
            w = str(w).strip().lower()
            if w:
                new_words.add(w)
    # 09/15 教訓：只用 22 類微調，模型會忘掉通用詞（road sign 0.83 → 0.23），
    # 執行期靠開放詞彙找東西反而變差。把眼睛平常在盯的基礎詞也放進類別表（沒框的就當負樣本）。
    BASE_VOCAB = ["wall", "cabinet", "shelf", "table", "chair", "box", "person", "cat", "dog", "rabbit",
                  "counter desk", "door", "window", "printer", "coffee machine", "computer monitor",
                  "road sign", "sign", "entrance", "tunnel", "gate", "tree", "fence", "bench"]
    for w in BASE_VOCAB:
        if w not in classes:
            classes.append(w)
    final_names = []
    for c in classes:
        n = rename.get(c, c)
        if n not in final_names:
            final_names.append(n)
    def _ok(w):
        w = str(w or "").strip().lower()
        return bool(w) and len(w) <= 30 and all(ord(c) < 128 for c in w) and 1 <= len(w.split()) <= 3
    for w in sorted(x for x in new_words if _ok(x)):
        if w not in final_names:
            final_names.append(w)
    if len(final_names) > MAX_CLASSES:
        # 09/15 教訓：中文長句灌進類別表 → 218 類、mAP50 從 0.51 掉到 0.18。類別太多就不要訓練。
        raise SystemExit(f"類別 {len(final_names)} 個太多（上限 {MAX_CLASSES}），先清乾淨 眼睛更正.jsonl 再訓練")
    old2new = {i: final_names.index(rename.get(c, c)) for i, c in enumerate(classes)}

    imgs = sorted(f for f in os.listdir(os.path.join(base, "images")) if f.endswith(".jpg"))
    if smoke:
        imgs = imgs[-60:]
    else:
        imgs = imgs[-MAX_IMAGES:]          # 只拿最近的，筆電 GPU 不要一次吃五小時
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    out = os.path.join(HERE, "專案", game, "素材", "資料集", stamp)
    for sub in ("images/train", "images/val", "labels/train", "labels/val"):
        os.makedirs(os.path.join(out, sub), exist_ok=True)
    random.seed(7)
    random.shuffle(imgs)
    n_val = max(1, len(imgs) // 10)
    # 補框：他說沒框到的詞，用 YOLO-World 回頭在有註記的畫面上找
    need_fill = {}
    for c in corr:
        fr = c.get("畫面")
        if fr and c.get("沒框到"):
            need_fill.setdefault(fr, set()).update(str(w).strip().lower() for w in c["沒框到"])
    filler = None
    if need_fill and not smoke:
        try:
            from ultralytics import YOLOWorld
            filler = YOLOWorld(os.environ.get("GAMER_VISION_MODEL", os.path.join(HERE, "yolov8s-world.pt")))
        except Exception as e:  # noqa: BLE001
            log(f"補框模型載入失敗（略過補框）：{e}")
    filled = 0
    for i, f in enumerate(imgs):
        stem = f[:-4]
        split = "val" if i < n_val else "train"
        shutil.copy2(os.path.join(base, "images", f), os.path.join(out, "images", split, f))
        rows = []
        lp = os.path.join(base, "labels", stem + ".txt")
        if os.path.exists(lp):
            for line in io.open(lp, encoding="utf-8"):
                parts = line.split()
                if len(parts) == 5:
                    try:
                        parts[0] = str(old2new[int(parts[0])])
                        rows.append(" ".join(parts))
                    except (KeyError, ValueError):
                        pass
        if filler is not None and stem in need_fill:
            try:
                words = sorted(need_fill[stem])
                filler.set_classes(words)
                r = filler.predict(os.path.join(base, "images", f), imgsz=640, conf=0.3, verbose=False)[0]
                W, H = r.orig_shape[1], r.orig_shape[0]
                for box, cls in zip(r.boxes.xyxy.tolist(), r.boxes.cls.tolist()):
                    x0, y0, x1, y1 = box
                    name = words[int(cls)]
                    rows.append("%d %.6f %.6f %.6f %.6f" % (final_names.index(name), (x0 + x1) / 2 / W,
                                                            (y0 + y1) / 2 / H, (x1 - x0) / W, (y1 - y0) / H))
                    filled += 1
            except Exception as e:  # noqa: BLE001
                log(f"補框失敗 {stem}：{e}")
        with io.open(os.path.join(out, "labels", split, stem + ".txt"), "w", encoding="utf-8") as fh:
            fh.write("\n".join(rows) + ("\n" if rows else ""))
    yaml = os.path.join(out, "data.yaml")
    with io.open(yaml, "w", encoding="utf-8") as fh:
        fh.write(f"path: {out}\ntrain: images/train\nval: images/val\nnames:\n")
        for i, n in enumerate(final_names):
            fh.write(f"  {i}: {n}\n")
    log(f"資料集：{len(imgs)} 張（val {n_val}）、{len(final_names)} 類、更正 {len(rename)} 條、"
        f"新詞 {len(new_words)} 個、補框 {filled} 個 → {out}")
    return yaml, final_names, len(imgs), out


def train(game, epochs, imgsz, smoke):
    yaml, names, n_img, out = build_dataset(game, smoke)
    from ultralytics import YOLOWorld
    base_w = os.path.join(HERE, "weights", "eyes_trained.pt")
    src = base_w if os.path.exists(base_w) else "yolov8s-worldv2.pt"      # 接著上次的繼續學
    log(f"從 {src} 開始微調，{epochs} epochs、imgsz {imgsz}")
    model = YOLOWorld(src)
    t0 = time.time()
    res = model.train(data=yaml, epochs=epochs, imgsz=imgsz, batch=8, device=0, workers=2,
                      project=os.path.join(HERE, "專案", game, "素材", "訓練"),
                      name=os.path.basename(out), exist_ok=True, verbose=False, plots=False)
    best = os.path.join(str(res.save_dir), "weights", "best.pt")
    if not os.path.exists(best):
        best = os.path.join(str(res.save_dir), "weights", "last.pt")
    os.makedirs(os.path.join(HERE, "weights"), exist_ok=True)
    dst = base_w if not smoke else os.path.join(HERE, "weights", "eyes_smoke.pt")
    shutil.copy2(best, dst)
    mins = (time.time() - t0) / 60
    metrics = {}
    try:
        metrics = {k: round(float(v), 3) for k, v in res.results_dict.items()}
    except Exception:  # noqa: BLE001
        pass
    mem = os.path.join(HERE, "專案", game, "記憶")
    with io.open(os.path.join(mem, "訓練紀錄.md"), "a", encoding="utf-8") as fh:
        fh.write(f"## {datetime.now().strftime('%m/%d %H:%M')}{'（冒煙測試）' if smoke else ''}\n"
                 f"- 資料：{n_img} 張、{len(names)} 類｜{epochs} epochs、imgsz {imgsz}｜{mins:.1f} 分鐘\n"
                 f"- 成績：{metrics}\n- 權重：{dst}\n\n")
    if not smoke:
        json.dump({"上次訓練": datetime.now().strftime("%m/%d %H:%M"), "張數": n_img, "類別": names,
                   "成績": metrics, "權重": dst},
                  io.open(os.path.join(mem, "訓練狀態.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        try:
            os.remove(os.path.join(mem, "訓練請求.json"))
        except OSError:
            pass
    log(f"✅ 訓練完成 {mins:.1f} 分鐘 → {dst}　成績 {metrics}")
    return dst


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", default="動物醫院")
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--imgsz", type=int, default=416)
    ap.add_argument("--smoke", action="store_true")
    a = ap.parse_args()
    if a.smoke:
        a.epochs, a.imgsz = 1, 320
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    train(a.game, a.epochs, a.imgsz, a.smoke)
