# gamer-ai-finetune — Qwen3.8-27B LoRA 微調（gamer AI 的「深度記憶」）

**目的**：把 gamer AI（Roblox 代理）**不會變的**基礎操作、狀態判斷、成功行為、學習方法、輸出格式燒進權重，
之後系統提示可以拿掉那二十幾份基礎操作文件。會變的（數字、作法、遊戲知識）繼續走文件，不燒。
訓練資料由 Windows 端 `gamer AI/build_finetune_set.py`＋`gen_rule_qa.py` 產生，放在 `gamer AI/訓練資料/finetune/`，
用 scp 同步到這裡的 `data/`。資料裡**不准有任何一款遊戲的名詞**（2026-09-26 使用者定的）。

## 為什麼不是微調現在的大腦
現在的大腦 Qwen3.8-Flash-Next 是 512 專家 MoE，NVFP4 就 99 GB、原始權重約 200 GB，這台 119 GiB 放不下訓練。
使用者 2026-09-26 選 **Qwen3.8-27B**（dense、看得懂圖、bf16 約 54 GB）當微調底模，訓練完跟 Flash-Next 實戰 A/B。

## 檔案
| 檔 | 用途 |
|---|---|
| `Dockerfile` | NGC `nvcr.io/nvidia/pytorch:26.01-py3` ＋ transformers／peft／accelerate |
| `docker-compose.yml` | `train`（LoRA SFT）與 `merge`（合併成 bf16 完整權重）兩個 service，參數走 `.env` |
| `train_lora.py` | 只訓語言模型的線性層（視覺編碼器不動），只對 assistant 算 loss；輸出 adapter、各類 val loss `報告.json`、抽答 `樣本.txt` |
| `merge_lora.py` | adapter 合併回底模 → `out/<run>/merged`（vLLM 直接服務） |
| `data/` | `<類>_{train,val}.jsonl`（A_qa 規則問答、B 狀態判斷、C 成功軌跡、D 錯誤→修正、E 輸出格式、F 學習方法） |
| `out/<run>/` | adapter／ckpt／報告.json／樣本.txt／merged |
| `download.log` | 底模下載紀錄（`huggingface_hub.snapshot_download("Qwen/Qwen3.8-27B")`，進 HF cache） |

## 怎麼跑
```bash
cd ~/projects/gamer-ai-finetune
cp .env.example .env                 # 改 RUN_NAME／EPOCHS 等
~/projects/Qwen3.8-Flash-Next-Single-DGX-Spark/stop.sh   # ⚠ 訓練前一定先停大腦，兩個一起放不下
docker compose build
docker compose run --rm train        # 幾小時；先用 --sample 200 試跑
docker compose run --rm merge        # 要拿去服務時才合併
~/projects/Qwen3.8-Flash-Next-Single-DGX-Spark/start.sh  # 訓完把大腦開回來（gamer AI 要用）
```
試跑：`docker compose run --rm train python /app/train_lora.py --sample 200 --epochs 1 --out /out/smoke`

## run2（2026-10-13）：把 10/12 的新規則燒進去
使用者 10/13 說「規則太多了」、要微調；查過千問開源沒有比 27B 更強又放得下的（Flash-Next 180B 實測訓不了），留在 Qwen3.8-27B 從底模重訓。
- 資料集差異（Windows 端 `build_finetune_set.py`，備份 `.bak_1013`）：
  - A：`01_指令等級表.md` 一條一段（以前沒有【】整份被跳過），38 條收進 36 條；新段落 55 段用 `gen_rule_qa.py --src A_new.jsonl --append` 生成問答（QA_MODEL=gamer-27b-ft-run1，四路並行）追加到 A_qa。
  - F：加放棄／卡牆／走過頭三種檢討。
  - **G 四欄想（新）**：思考流程.jsonl 裡 `想` 是「因為｜所以要｜動作｜結果應該是」的輪，配步驟日誌畫面變化 ≥5、合規則、無遊戲名詞；system 提示寫明四欄格式。
- `train_lora.py --cap C=2000,F=2000`（新參數，抽樣控制時間；C 4412／F 4903 筆太多），`.env`：RUN_NAME=run2、CLASSES=A_qa,B,C,E,F,G、REPEAT=A_qa=2,B=3,G=2。
- 跑法同 run1：`docker compose run -d train` → `merge` → `quant` → `up -d serve`（served name `gamer-27b-ft-run2`）。
- **看進度**：detached 跑的時候 `docker logs` 不會有進度條與 loss（stdout 緩衝），用 `docker exec --privileged train-run2 pip install -q py-spy && docker exec --privileged train-run2 py-spy dump --pid 1 --locals | grep update_step`（總步數＝筆數÷grad_accum×epochs），或看 `out/run2/ckpt/checkpoint-*`（每 200 步存）。

## 驗收
1. `報告.json` 各類 val loss／ppl，跟訓練前的底模比（先跑一次 `--epochs 0` 拿基準）。
2. `樣本.txt` 肉眼看 A_qa／B 的回答有沒有照規則、有沒有冒出遊戲名詞。
3. 實戰 A/B：Windows 端 `開始玩.ps1` 指到合併後的模型（另開一個 vLLM，port 另定），各跑兩小時，
   比每小時進場次數、純 dy 輪比例、死亡數、卡住比例。

## 注意
- D 類（錯誤→修正）目前 `chosen` 還是檢討文字不是動作，**還沒進 SFT**；之後轉成 DPO 對再用。
- `max-len 3072` 是因為第一版 C 類輸入只有一句「看到」；2026-09-26 起代理每輪把完整面板存進 `記憶/面板/*.jsonl.gz`，
  之後重撈的 C 類會是一到兩萬 token，要把 `MAX_LEN` 調到 16k 以上、`GRAD_ACCUM` 調大。
- Port 對照表：這個專案不常駐、不佔 port；合併後要服務再另起 vLLM。
