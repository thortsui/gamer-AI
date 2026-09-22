# DGX 部署紀錄：Qwen3.8-Flash-Next（單台 DGX Spark，TP=1）

> 依照 GitHub `MiaAI-Lab/Qwen3.8-Flash-Next-Single-DGX-Spark` 的 README 部署。
> 這份是「我們這台」的實際紀錄；官方說明以 DGX 上 `~/projects/Qwen3.8-Flash-Next-Single-DGX-Spark/README.md` 為準。

## 官方四步

```bash
cp .env.sample .env        # 需要時改 IMAGE / HF_TOKEN
./download.sh              # 抓 ~99 GB 權重（可中斷續傳）
./start.sh                 # ~10-12 分鐘到 /health，服務在 :8888
./stop.sh                  # 優雅停止（容器 + 看門狗）
```

## 這台的實際流程（2026-09-06）

| 步驟 | 指令 | 結果 |
|---|---|---|
| 1. clone | `cd ~/projects && git clone https://github.com/MiaAI-Lab/Qwen3.8-Flash-Next-Single-DGX-Spark.git` | commit `ef1af5f` |
| 2. 設定 | `cp .env.sample .env` | 沒改任何值（262k、MTP 3、FP8 KV、HOST_RESERVE 26、port 8888） |
| 3. 下載 | `nohup ./download.sh > logs/download.log 2>&1 &` | 主機有 `huggingface_hub` 1.4.1，直接用；51 個檔、約 30～58 MB/s |
| 4. 拉映像 | `docker pull vllm/vllm-openai:qwen38-flash-next` | 與下載並行 |
| 5. 讓記憶體 | `docker stop vllm-qwen3.8-27b-pet` | 寵物模型（8102）先停 |
| 6. 啟動 | `./start.sh` | 第一次建 PLE 表（27 GB）後才起容器 |
| 7. 驗證 | `curl localhost:8888/v1/models`＋README 的 sanity test | 見工作日誌 |

## `.env` 重點（沿用 .env.sample）

| 鍵 | 值 | 意思 |
|---|---|---|
| `IMAGE` | `vllm/vllm-openai:qwen38-flash-next` | 專用映像（含 PLE / QSA 補丁的 vLLM） |
| `SERVED_MODEL_NAME` | `qwen3.8-flash-next` | API 的模型名 |
| `YARN` | 0 | 原生 262k；要 512k 改 1 |
| `MAX_MODEL_LEN` | 262144 | |
| `MTP_NUM_SPECULATIVE_TOKENS` | 3 | 投機解碼，0 關閉省 1.5 GiB |
| `KV_TARGET_GIB` | 20 | 願望值，實際被主機保留量壓到約 16.7 GiB |
| `HOST_RESERVE_GIB` | 26 | 給主機留的，**保命的那個值，別調低** |
| `KV_CACHE_DTYPE` | fp8 | KV 池 ~1.85 倍 |
| `MAX_NUM_SEQS` | 4 | 同時請求數 |
| `PORT` | 8888 | |

## 安全規則（README 摘要，照做就不會把機器搞掛）

- GPU 預算從主機端算：`MemTotal − HOST_RESERVE_GIB`，`start.sh` 會自己算並印出來。
- 主機 `MemAvailable` 要維持 ≥ ~10 GiB，掉到 6 以下看門狗會把容器停掉（不會讓整台當機）。
- **絕不**設 `PLE_OFFLOAD=false`（會把主機搞到 hang）。
- 512k 以上（1M）沒人在這台測過，別當第一個。
- `comfy-h3.service` 會搶 8888 埠；這台目前沒有這個服務。
- `docker --memory` 管不到 GPU 端，靠的是 `--gpu-memory-utilization`。

## 回滾

```bash
cd ~/projects/Qwen3.8-Flash-Next-Single-DGX-Spark && ./stop.sh
docker start vllm-qwen3.8-27b-pet            # 寵物 8102
docker start vllm-qwen3.8-27b-abliterated    # Minecraft 8101
```

權重與 PLE 表留在 `~/.cache/huggingface/hub/models--Mia-AiLab--Qwen3.8-Flash-Next-NVFP4/`（99 GB）和 `~/.cache/vllm/ple_cache/`（27 GB），不用時可刪。
