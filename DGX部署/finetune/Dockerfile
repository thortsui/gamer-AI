# gamer AI fine-tune 環境（DGX Spark / GB10 / aarch64）
# 底層用 NGC 的 PyTorch 映像（已含 CUDA 13 的 torch），只加訓練要的幾個套件。
FROM nvcr.io/nvidia/pytorch:26.01-py3

ENV PIP_NO_CACHE_DIR=1 HF_HUB_ENABLE_HF_TRANSFER=0 TOKENIZERS_PARALLELISM=false
RUN pip install --no-deps -U "transformers>=4.57" && \
    pip install -U peft accelerate datasets huggingface_hub safetensors sentencepiece

WORKDIR /app
COPY train_lora.py merge_lora.py /app/
CMD ["python", "/app/train_lora.py", "--help"]
