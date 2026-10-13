# -*- coding: utf-8 -*-
"""
train_lora.py — 用 gamer AI 的 fine-tune 資料集（訓練資料/finetune/*.jsonl，chat 格式）對 Qwen3.8-27B 做 LoRA SFT。

只訓練語言模型那一半（視覺編碼器不動）；只對 assistant 那段算 loss。
資料每筆：{"messages":[{"role":"system",...},{"role":"user",...},{"role":"assistant",...}], "class": "C", ...}
"""
import argparse
import glob
import json
import math
import os
import random
import re
import time

import torch
from torch.utils.data import Dataset
from transformers import (AutoModelForImageTextToText, AutoProcessor, Trainer, TrainingArguments,
                          default_data_collator)
from peft import LoraConfig, get_peft_model


def load_rows(data_dir, classes, split, repeat):
    rows = []
    for cls in classes:
        p = os.path.join(data_dir, f"{cls}_{split}.jsonl")
        if not os.path.exists(p):
            print(f"（沒有 {p}，跳過）")
            continue
        r = [json.loads(l) for l in open(p, encoding="utf-8")]
        r = [x for x in r if x.get("messages") and x["messages"][-1]["role"] == "assistant"]
        k = repeat.get(cls, 1) if split == "train" else 1
        print(f"{cls}_{split}: {len(r)} 筆 ×{k}")
        rows += r * k
    random.shuffle(rows)
    return rows


class ChatDS(Dataset):
    """prompt（system+user）不算 loss，只算 assistant。超過 max_len 的從 prompt 前面截。"""

    def __init__(self, rows, tok, max_len):
        self.items = []
        n_trunc = 0
        for r in rows:
            msgs = r["messages"]
            # enable_thinking=False：Qwen3.5 的模板會在 assistant 開頭放空的 <think></think>，
            # 答案直接接在後面；代理實戰的反射／快思也是關思考送的，兩邊一致。
            prompt = tok.apply_chat_template(msgs[:-1], tokenize=False, add_generation_prompt=True,
                                             enable_thinking=False)
            full = prompt + msgs[-1]["content"] + tok.eos_token
            p_ids = tok(prompt, add_special_tokens=False)["input_ids"]
            f_ids = tok(full, add_special_tokens=False)["input_ids"]
            if len(f_ids) > max_len:
                cut = len(f_ids) - max_len
                f_ids, p_ids = f_ids[cut:], p_ids[cut:]
                n_trunc += 1
            labels = [-100] * len(p_ids) + f_ids[len(p_ids):]
            self.items.append((f_ids, labels))
        print(f"  {len(self.items)} 筆，截掉前段的 {n_trunc} 筆")

    def __len__(self):
        return len(self.items)

    def __getitem__(self, i):
        ids, labels = self.items[i]
        return {"input_ids": ids, "labels": labels}


def collate(batch, pad_id):
    L = max(len(b["input_ids"]) for b in batch)
    ids = torch.full((len(batch), L), pad_id, dtype=torch.long)
    lab = torch.full((len(batch), L), -100, dtype=torch.long)
    att = torch.zeros((len(batch), L), dtype=torch.long)
    for i, b in enumerate(batch):
        n = len(b["input_ids"])
        ids[i, :n] = torch.tensor(b["input_ids"])
        lab[i, :n] = torch.tensor(b["labels"])
        att[i, :n] = 1
    return {"input_ids": ids, "labels": lab, "attention_mask": att}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="Qwen/Qwen3.8-27B")
    ap.add_argument("--data", default="/data")
    ap.add_argument("--out", default="/out/run1")
    ap.add_argument("--classes", default="A_qa,B,C,E,F")
    ap.add_argument("--repeat", default="A_qa=2,B=4", help="訓練集重複倍數，例如 A_qa=2,B=4")
    ap.add_argument("--epochs", type=float, default=2)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--r", type=int, default=16)
    ap.add_argument("--alpha", type=int, default=32)
    ap.add_argument("--max-len", type=int, default=3072)
    ap.add_argument("--grad-accum", type=int, default=16)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--sample", type=int, default=0, help="只拿前 N 筆試跑")
    args = ap.parse_args()
    random.seed(args.seed)
    torch.manual_seed(args.seed)
    classes = [c for c in args.classes.split(",") if c]
    repeat = {k: int(v) for k, v in (kv.split("=") for kv in args.repeat.split(",") if "=" in kv)}
    os.makedirs(args.out, exist_ok=True)

    print(f"載入 {args.model} …")
    proc = AutoProcessor.from_pretrained(args.model)
    tok = getattr(proc, "tokenizer", proc)
    model = AutoModelForImageTextToText.from_pretrained(args.model, dtype=torch.bfloat16, device_map="cuda")
    model.config.use_cache = False
    model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
    model.enable_input_require_grads()

    # 只掛在語言模型的線性層上；名字依模型版本不同，先用正則找，找不到再退回一般名字
    names = [n for n, m in model.named_modules() if isinstance(m, torch.nn.Linear)]
    pat = re.compile(r"(language_model|text_model|\.layers\.\d+\.(self_attn|mlp))")
    want = ("q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj")
    targets = [n for n in names if n.endswith(want) and pat.search(n) and "visual" not in n and "vision" not in n]
    if not targets:
        targets = list(want)
    print(f"LoRA 掛在 {len(targets)} 個線性層（例如 {targets[:2]} …）")
    model = get_peft_model(model, LoraConfig(r=args.r, lora_alpha=args.alpha, lora_dropout=0.05,
                                             target_modules=targets, task_type="CAUSAL_LM"))
    model.print_trainable_parameters()

    train_rows = load_rows(args.data, classes, "train", repeat)
    val_rows = load_rows(args.data, classes, "val", {})
    if args.sample:
        train_rows, val_rows = train_rows[: args.sample], val_rows[: max(8, args.sample // 10)]
    print("組訓練集…"); train_ds = ChatDS(train_rows, tok, args.max_len)
    print("組驗證集…"); val_ds = ChatDS(val_rows, tok, args.max_len)
    pad_id = tok.pad_token_id if tok.pad_token_id is not None else tok.eos_token_id

    # transformers 5 拿掉了 warmup_ratio，自己換算成步數（3%）
    total_steps = max(1, int(math.ceil(len(train_ds) / args.grad_accum) * args.epochs))
    kw = dict(
        output_dir=os.path.join(args.out, "ckpt"), num_train_epochs=args.epochs, learning_rate=args.lr,
        per_device_train_batch_size=1, per_device_eval_batch_size=1, gradient_accumulation_steps=args.grad_accum,
        lr_scheduler_type="cosine", warmup_steps=max(1, int(total_steps * 0.03)), weight_decay=0.0, bf16=True,
        logging_steps=10, eval_strategy="steps", eval_steps=200, save_strategy="steps", save_steps=200,
        save_total_limit=2, report_to=[], seed=args.seed, dataloader_num_workers=2,
        remove_unused_columns=False, group_by_length=True,
    )
    # transformers 5 拿掉了一些參數（warmup_ratio、group_by_length…），不支援的直接略過，別為了一個參數重載 7 分鐘權重
    import inspect
    ok = set(inspect.signature(TrainingArguments.__init__).parameters)
    dropped = [k for k in kw if k not in ok]
    if dropped:
        print(f"（這版 transformers 不認得 {dropped}，略過）")
    targs = TrainingArguments(**{k: v for k, v in kw.items() if k in ok})
    trainer = Trainer(model=model, args=targs, train_dataset=train_ds, eval_dataset=val_ds,
                      data_collator=lambda b: collate(b, pad_id))
    t0 = time.time()
    trainer.train()
    print(f"訓練完 {(time.time() - t0) / 60:.1f} 分鐘")
    model.save_pretrained(os.path.join(args.out, "adapter"))
    tok.save_pretrained(os.path.join(args.out, "adapter"))

    # 各類驗證 loss
    report = {}
    for cls in classes:
        rows = load_rows(args.data, [cls], "val", {})
        if not rows:
            continue
        ds = ChatDS(rows[:200], tok, args.max_len)
        m = trainer.evaluate(eval_dataset=ds)
        report[cls] = {"val_loss": round(m["eval_loss"], 4), "ppl": round(math.exp(m["eval_loss"]), 2), "n": len(ds)}
        print(cls, report[cls])
    json.dump({"args": vars(args), "val": report}, open(os.path.join(args.out, "報告.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    # 抽幾題讓它答，肉眼看
    model.eval()
    with open(os.path.join(args.out, "樣本.txt"), "w", encoding="utf-8") as fh:
        for cls in ("A_qa", "B"):
            rows = load_rows(args.data, [cls], "val", {})[:8]
            for r in rows:
                prompt = tok.apply_chat_template(r["messages"][:-1], tokenize=False, add_generation_prompt=True,
                                                 enable_thinking=False)
                ids = tok(prompt, return_tensors="pt").to("cuda")
                with torch.no_grad():
                    out = model.generate(**ids, max_new_tokens=160, do_sample=False)
                ans = tok.decode(out[0][ids["input_ids"].shape[1]:], skip_special_tokens=True)
                fh.write(f"[{cls}] Q: {r['messages'][1]['content'][:200]}\n   標準答案: {r['messages'][2]['content'][:200]}"
                         f"\n   它答: {ans.strip()[:300]}\n\n")
    print("→", args.out)


if __name__ == "__main__":
    main()
