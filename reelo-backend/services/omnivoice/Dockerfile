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
#
# torch MUST be >= 2.7: transformers (pulled in by omnivoice, >=5.3.0) imports
# torch.float8_e8m0fnu at module load, and that dtype only exists from torch 2.7.
# On the old 2.4.1 base, `from transformers import HiggsAudioV2TokenizerModel`
# raised AttributeError, so /clone returned 503 ("OmniVoice runtime unavailable").
#
# We pin the CUDA 12.6 build (NOT 12.8): PyTorch 2.8 + CUDA 12.8 removed Pascal
# (sm_61) kernels, and the GPU host runs a GTX 1080 Ti (sm_61). torch 2.7.x on
# cu126 still ships Pascal kernels, so it both imports transformers AND runs on
# this GPU. Going past torch 2.7 here would need a newer (sm_70+) card.
FROM pytorch/pytorch:2.7.1-cuda12.6-cudnn9-runtime

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
#
# transformers is pinned: this host is locked to torch 2.7 (Pascal GPU), and a
# future transformers that requires torch >= 2.8 would silently break the build.
# 5.10.2 is the version verified working against torch 2.7.x on this GPU.
RUN pip install \
    omnivoice \
    "transformers==5.10.2" \
    soundfile \
    "fastapi>=0.115" \
    "uvicorn[standard]>=0.30" \
    "python-multipart>=0.0.9"

COPY server.py /app/server.py

EXPOSE 8002

# Model loads lazily on first /clone, so startup is instant and /health works
# immediately (also used by the compose healthcheck).
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8002"]
