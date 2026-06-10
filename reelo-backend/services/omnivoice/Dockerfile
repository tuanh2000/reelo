# syntax=docker/dockerfile:1
# =============================================================================
# OmniVoice GPU microservice (k2-fsa/OmniVoice voice cloning).
#
# REQUIRES AN NVIDIA GPU + nvidia-container-toolkit on the host. Run it via the
# `omnivoice` service in docker-compose.prod.yml (which sets `gpus: all`). This
# image bundles CUDA + PyTorch; the model weights (Qwen3-0.6B based) are pulled
# from HuggingFace on the FIRST /clone call (cached in the hf-cache volume).
#
# Build context = reelo-backend/services/omnivoice/. Build with:
#   docker build -f reelo-backend/services/omnivoice/Dockerfile \
#     -t ghcr.io/tuanh2000/reelo-omnivoice:TAG \
#     reelo-backend/services/omnivoice
#
# NOTE: large image (CUDA+torch, several GB) and the first request downloads the
# model — see DEPLOY.md. Build/pull this on the GPU host (self-hosted runner),
# not on hosted CI.
# =============================================================================
# pytorch/pytorch ships CUDA + cuDNN + a matching torch/torchaudio already, so
# we don't re-install torch from requirements (it's already present and CUDA-built).
FROM pytorch/pytorch:2.4.1-cuda12.1-cudnn9-runtime

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/root/.cache/huggingface

# libsndfile is needed by soundfile for WAV I/O.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libsndfile1 ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# torch/torchaudio already come from the base image with the correct CUDA build,
# so install only the service deps (omnivoice, soundfile, server stack).
RUN pip install \
    omnivoice \
    soundfile \
    "fastapi>=0.115" \
    "uvicorn[standard]>=0.30" \
    "python-multipart>=0.0.9"

COPY server.py /app/server.py

EXPOSE 8002

# Model loads lazily on first /clone, so startup is instant and /health works
# immediately (also used by the compose healthcheck).
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8002"]
