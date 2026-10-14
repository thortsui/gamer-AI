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
- **12:2x 使用者「訓練所有規則進去」**：A 類一段不丟（夾遊戲名詞也留）、動作表切 6 段、新增 **A_text 背規則**（問規則標題→答原文，×3）；CLASSES=A_qa,A_text,B,C,E,F,G。遊戲個體文件仍不燒。
- `train_lora.py --cap C=2000,F=2000`（新參數，抽樣控制時間；C 4412／F 4903 筆太多），`.env`：RUN_NAME=run2、CLASSES=A_qa,B,C,E,F,G、REPEAT=A_qa=2,B=3,G=2。
- **A_app 即時應用考題**（2026-10-13 15:xx 使用者：「新增一系列考題——互動時用什麼參數、經過什麼計算；緊急情況例如迷路；找不知道在哪的東西」）：`考題_即時應用.py` 手寫 93 題＋數字題 262 題（給刻度／框高／偏角，算 dx、dy、秒數、拆兩段）共 355 筆，答案全照 核心/ 規則、0 筆遊戲名詞；`.env` CLASSES 加 A_app、REPEAT A_app=2。**run2 第三次開訓（12:54）時還沒有這一類**，要進下一次訓練。
- **A_stuck 卡住解法考題**（2026-10-13 16:xx 使用者：「卡牆這些的，他自己整理出來容易卡住的方式，也要問他解決方法；這些等 run3 再訓練」）：`考題_卡住.py`。題目來源＝他自己寫的檢討（專案/*/記憶/{卡住,卡牆,走過頭,死亡,放棄}檢討.md 的原因／錯在哪，5,559 筆用關鍵字歸成 32 種卡法，每種抽 9 筆）＋他自己訂的扣分規則（扣分檢討.json 的「為什麼」）；答案照 核心/ 規則寫（他自訂規則跟核心抵觸的不採用：「dy 一律填 0 只用 scroll」「含 look 的輪必含點字」「s/a 一次按滿 1.5s」）。365 筆（總結 64／實例 272／扣分 29），遮名詞後 0 筆遊戲名詞。

## run3（2026-10-13 晚上，自動接在 run2 後面）
- `.env`：RUN_NAME=run3、CLASSES=A_qa,A_text,A_act,A_app,A_stuck,B,C,E,F,G、REPEAT=A_qa=2,A_text=3,A_act=3,A_app=2,A_stuck=2,B=3,G=2、CAP 同 run2。約 10.6k 筆／1,330 步，23.6 秒一步 ≈ 8.7 小時。
- `chain_run3.sh`（nohup，log 在 `chain_run3.log`）：等 `train-run2` 結束 → drop_caches → `docker compose run -d --name train-run3 train`。run2 **不先** merge／quant（adapter 留在 out/run2，之後要比對再 `RUN_NAME=run2 docker compose run --rm merge`）。
- run3 訓完：看 out/run3/報告.json 與 樣本.txt 跟 run2 比 → `docker compose run --rm merge` → `docker compose run --rm quant` → `docker compose up -d serve`（served name `gamer-27b-ft-run3`）→ flow_agent 模型名改 run3。
- 跑法同 run1：`docker compose run -d train` → `merge` → `quant` → `up -d serve`（served name `gamer-27b-ft-run2`）。
- **結果**：run2 10/13 21:13 訓完、run3 10/14 07:04 訓完（都 exit 0）。val loss：A_qa 2.178／A_text 1.682／A_act 1.591／A_app 0.346／A_stuck 0.037／B 0.077／C 0.185／F 1.323／G 0.433（run2：2.195／1.717／1.823／–／–／0.072／0.193／1.292／0.433）。報告副本在本機 `訓練資料/finetune/報告/`。
- 10/14 21:11 `chain_merge3.sh`（nohup，log `chain_merge3.log`）：merge → drop_caches → quant → `up -d serve`（gamer-27b-ft-run3）。之後要換 run 就改 `.env` 的 RUN_NAME 再 `down serve`／`up -d serve`。
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
