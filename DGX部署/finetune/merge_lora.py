# -*- coding: utf-8 -*-
"""merge_lora.py — 把 LoRA adapter 合併回 Qwen3.8-27B，存成完整 bf16 權重（給 vLLM 直接服務）。"""
import argparse
import shutil
import os

import torch
from transformers import AutoModelForImageTextToText, AutoProcessor
from peft import PeftModel


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="Qwen/Qwen3.8-27B")
    ap.add_argument("--adapter", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    print("載入底模…")
    model = AutoModelForImageTextToText.from_pretrained(args.model, dtype=torch.bfloat16, device_map="cpu")
    print("套 adapter 並合併…")
    model = PeftModel.from_pretrained(model, args.adapter).merge_and_unload()
    os.makedirs(args.out, exist_ok=True)
    model.save_pretrained(args.out, safe_serialization=True, max_shard_size="5GB")
    AutoProcessor.from_pretrained(args.model).save_pretrained(args.out)
    for f in ("chat_template.jinja", "chat_template.json"):
        src = os.path.join(args.adapter, f)
        if os.path.exists(src):
            shutil.copy(src, args.out)
    print("→", args.out)


if __name__ == "__main__":
    main()
