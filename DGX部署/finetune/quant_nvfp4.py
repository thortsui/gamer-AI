# -*- coding: utf-8 -*-
"""
quant_nvfp4.py — 把合併後的 Qwen3.8-27B（bf16）用 NVIDIA ModelOpt 壓成 NVFP4，給 vLLM 服務（Blackwell 原生 4-bit，解碼約再快一倍）。

  python quant_nvfp4.py --model /out/run1/merged --out /out/run1/nvfp4 --calib /data --n-calib 256

只量化語言模型的線性層（視覺編碼器與 lm_head 不動）。校準資料用我們自己的訓練集 prompt（文字），跟實戰同分布。
⚠ 要 GPU、要約 60 GB 記憶體（bf16 權重 54 GB＋校準），跟正在服務的大腦放不下同一台——等他收工再跑。
"""
import argparse
import glob
import json
import os
import random

import torch
from transformers import AutoModelForImageTextToText, AutoProcessor


def calib_texts(data_dir, n, tok):
    rows = []
    for p in glob.glob(os.path.join(data_dir, "*_train.jsonl")):
        rows += [json.loads(l) for l in open(p, encoding="utf-8")][:2000]
    random.seed(7)
    random.shuffle(rows)
    out = []
    for r in rows:
        msgs = r.get("messages")
        if not msgs:
            continue
        out.append(tok.apply_chat_template(msgs, tokenize=False, enable_thinking=False))
        if len(out) >= n:
            break
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--calib", default="/data")
    ap.add_argument("--n-calib", type=int, default=256)
    ap.add_argument("--max-len", type=int, default=1024)
    args = ap.parse_args()

    import modelopt.torch.quantization as mtq
    from modelopt.torch.export import export_hf_checkpoint

    print("載入 bf16 …")
    proc = AutoProcessor.from_pretrained(args.model)
    tok = getattr(proc, "tokenizer", proc)
    model = AutoModelForImageTextToText.from_pretrained(args.model, dtype=torch.bfloat16, device_map="cuda")
    model.eval()

    texts = calib_texts(args.calib, args.n_calib, tok)
    print(f"校準樣本 {len(texts)} 筆")

    cfg = mtq.NVFP4_DEFAULT_CFG
    # 視覺編碼器、lm_head、embedding 不量化（vLLM 的 modelopt 載入器預期只有語言模型線性層是 NVFP4）
    cfg["quant_cfg"]["*visual*"] = {"enable": False}
    cfg["quant_cfg"]["*vision*"] = {"enable": False}
    cfg["quant_cfg"]["*lm_head*"] = {"enable": False}
    cfg["quant_cfg"]["*embed*"] = {"enable": False}

    def forward_loop(m):
        with torch.no_grad():
            for i, t in enumerate(texts):
                ids = tok(t, return_tensors="pt", truncation=True, max_length=args.max_len).to("cuda")
                m(**ids)
                if i % 32 == 0:
                    print(f"  校準 {i}/{len(texts)}", flush=True)

    print("量化中 …")
    model = mtq.quantize(model, cfg, forward_loop)
    mtq.print_quant_summary(model)
    print("匯出 HF checkpoint …")
    os.makedirs(args.out, exist_ok=True)
    export_hf_checkpoint(model, export_dir=args.out)
    proc.save_pretrained(args.out)
    for f in ("chat_template.jinja",):
        src = os.path.join(args.model, f)
        if os.path.exists(src):
            import shutil
            shutil.copy(src, args.out)
    print("→", args.out)


if __name__ == "__main__":
    main()
