FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DEFAULT_TIMEOUT=300 \
    PIP_RETRIES=10
WORKDIR /app

# 系统依赖：libgomp（torch/transformers 需要）、curl（healthcheck）
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 curl && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# Linux PyPI 的 torch 轮子会连带安装多 GB CUDA 运行库。先安装 CPU 轮子，
# 后续 requirements 解析会复用已满足的 torch==2.5.1。
RUN pip install --disable-pip-version-check "torch==2.5.1" \
        --index-url https://download.pytorch.org/whl/cpu --no-deps
RUN pip install --disable-pip-version-check -r requirements.txt

COPY . .

EXPOSE 8010
HEALTHCHECK --interval=15s --timeout=5s --retries=5 \
  CMD curl -fsS http://127.0.0.1:8010/health || exit 1

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8010"]
