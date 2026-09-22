# DGX 連線說明（gamer AI 版）

> 完整版手冊（含 YOLO 訓練、目錄地圖、踩坑總表）在
> `D:\Documents\ThorWorkSpace\Minecraft 幫手製作\DGX連線與使用手冊.md`。
> 這份只留「連上去」和「這個專案用的模型怎麼啟停」。
> 最後驗證：2026-09-06（當天 ssh 成功、部署 Qwen3.8-Flash-Next）。

---

## 1. 機器

| 項目 | 內容 |
|---|---|
| 機型 | NVIDIA **DGX Spark**（hostname `spark-7879`），GB10，**~119 GiB 統一記憶體**（CPU/GPU 共用），ARM aarch64 |
| 區網 IP | `192.168.0.126`（要在同一個 WiFi/區網） |
| 帳號 | `ethan` |
| 磁碟 | 3.7 TB，2026-09-06 剩 2.2 TB |
| 原則 | 所有模型都跑 Docker；主機 Python 不裝東西 |

## 2. 連線

```bash
ssh -i ~/.ssh/dgx_claude ethan@192.168.0.126
ssh -i ~/.ssh/dgx_claude ethan@192.168.0.126 "hostname && docker ps"
```

- 私鑰 `C:\Users\ethan\.ssh\dgx_claude`（不進 git、不上雲端、不貼對話）。換電腦要帶著它，步驟見完整版手冊第 2 節。
- 連不上：先 `ping 192.168.0.126`；通了但拒絕 → 金鑰沒放對。

以下 `$DGX` 代表 `ssh -i ~/.ssh/dgx_claude ethan@192.168.0.126`。

## 3. 這個專案的模型：Qwen3.8-Flash-Next（NVFP4）

| 項目 | 內容 |
|---|---|
| 來源 | GitHub `MiaAI-Lab/Qwen3.8-Flash-Next-Single-DGX-Spark`（AGPL-3.0）；權重 HF `Mia-AiLab/Qwen3.8-Flash-Next-NVFP4`（99 GB） |
| DGX 上的位置 | `~/projects/Qwen3.8-Flash-Next-Single-DGX-Spark/` |
| 容器名 | `vllm-fn-tp1` |
| 映像 | `vllm/vllm-openai:qwen38-flash-next` |
| 服務埠 | **8888**（OpenAI 相容 API：`http://192.168.0.126:8888/v1`），模型名 `qwen3.8-flash-next` |
| 特性 | 262k context、看圖／看影片、MTP 投機解碼、FP8 KV cache、預設**會思考** |
| 記憶體 | 幾乎吃滿整台（GPU 預算 ~95 GiB + 主機保留 26 GiB）→ **其他大模型容器必須先停** |
| 啟動時間 | 第一次要建 27 GB 的 PLE 表（一次性），之後每次約 10～12 分鐘到 `/health` |

```bash
# 啟動（會自己算記憶體預算、起看門狗、然後 tail log 直到 /health 通）
$DGX "cd ~/projects/Qwen3.8-Flash-Next-Single-DGX-Spark && ./start.sh"

# 只看預算、不啟動
$DGX "cd ~/projects/Qwen3.8-Flash-Next-Single-DGX-Spark && ./start.sh --no-launch"

# 檢查活著沒
$DGX "curl -s localhost:8888/v1/models"
curl -s http://192.168.0.126:8888/health          # 從筆電

# 停止（優雅，等它把 /dev/shm 清乾淨）
$DGX "cd ~/projects/Qwen3.8-Flash-Next-Single-DGX-Spark && ./stop.sh"

# 看 log
$DGX "docker logs --tail 50 vllm-fn-tp1"
$DGX "tail -5 ~/projects/Qwen3.8-Flash-Next-Single-DGX-Spark/logs/memwatch-vllm-fn-tp1.log"
```

### 啟動前一定要做

1. **停掉其他大模型**，不然 `start.sh` 會因為 MemAvailable 不夠直接拒絕：
   ```bash
   $DGX "docker ps --format '{{.Names}}: {{.Status}}'"
   $DGX "docker stop vllm-qwen3.8-27b-pet"          # 千問寵物用的（8102）
   $DGX "docker stop vllm-qwen3.8-27b-abliterated"  # Minecraft 用的（8101），沒開就略過
   ```
2. 等 `free -g` 的 available 回到 ~110 GiB 以上（GB10 是共用記憶體，最後一個 CUDA 容器停掉才會整個還回來）。

### 換回其他模型

這顆和寵物／Minecraft 的模型**不能同時開**（記憶體只有一份）。要換回去：

```bash
$DGX "cd ~/projects/Qwen3.8-Flash-Next-Single-DGX-Spark && ./stop.sh"
$DGX "docker start vllm-qwen3.8-27b-pet"           # 寵物（8102）
$DGX "docker start vllm-qwen3.8-27b-abliterated"   # 或 Minecraft（8101）
```

## 4. 呼叫時的坑

1. **預設會思考**：想快就每個請求加 `"chat_template_kwargs": {"enable_thinking": false}`；不關的話 `max_tokens` 至少給 400，不然回覆會空（答案還在 `reasoning` 欄位裡）。
2. **思考內容在 `reasoning` 欄位**，不在 `content`（`start.sh` 有開 `--reasoning-parser qwen3`）。
3. **別寫死模型名**：打 `/v1/models` 取。
4. **看圖**：用 OpenAI 的 `image_url` content-part（`data:` URI 或 http URL）就好；看圖時 MTP 會退回一般解碼，變慢是正常的。
5. **system 訊息只能一則、放最前面**（Qwen3.8 模板限制，多放會 400）。
6. **埠 8888**：DGX 上有個 `comfy-h3.service` 會監聽這個埠並自動拉起 ComfyUI；目前該服務不存在（`not-found`），若哪天出現，改 `PORT=8890 ./start.sh`。

## 5. 首次部署紀錄（2026-09-06）

```bash
cd ~/projects && git clone https://github.com/MiaAI-Lab/Qwen3.8-Flash-Next-Single-DGX-Spark.git
cd Qwen3.8-Flash-Next-Single-DGX-Spark
cp .env.sample .env                     # 沒改任何值
nohup ./download.sh > logs/download.log 2>&1 &     # 99 GB，約 40 MB/s
docker pull vllm/vllm-openai:qwen38-flash-next     # 先拉映像省時間
docker stop vllm-qwen3.8-27b-pet        # 記憶體讓出來
./start.sh                              # 第一次多 27 GB PLE 表建置
```

部署時機器狀態：`vllm-qwen3.8-27b-pet` 在跑（8102，寵物專案）、記憶體 used 33 / avail 85 GiB、磁碟剩 2.2 TB、Docker 29.1.3、`huggingface_hub` 1.4.1 已在主機 Python（`download.sh` 直接用它，不需容器）。
