# syntax=docker/dockerfile:1
# =============================================================================
# Reelo backend image — ONE image used for BOTH `web` (uvicorn) and `worker`
# (arq). The command differs per service in docker-compose.prod.yml.
#
# IMPORTANT: build context MUST be the REPOSITORY ROOT (video-generator/) so we
# can COPY both `reelo-backend/` and the skill `scripts/`. Build with:
#
#   docker build -f reelo-backend/Dockerfile -t ghcr.io/tuanh2000/reelo-backend:TAG .
#
# (note the trailing "." = repo root, and -f pointing at this file.)
# =============================================================================
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# ffmpeg: required by render.py (Module 2) + the skill voice/stub clients.
# (build-essential not needed — all wheels are prebuilt for slim/py3.11.)
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ---- Python deps (cached layer) --------------------------------------------
# Install backend deps from requirements.txt FIRST (better layer caching), then
# the extra deps the skill scripts need (requests, python-dotenv).
COPY reelo-backend/requirements.txt ./requirements.txt
RUN pip install -r requirements.txt \
    && pip install "requests>=2.31" "python-dotenv>=1.0"

# ---- Application code -------------------------------------------------------
# Backend package (respects reelo-backend/.dockerignore for .venv/__pycache__/…).
COPY reelo-backend/ /app/

# Skill: only the bits the worker shells out to. We deliberately COPY the
# narrow set (scripts/ + SKILL.md + references/) and NOT projects/ (large
# generated mp4/png ~38MB) or .venv/.
COPY skill-tao-video-Youtube-ton-giao/scripts/ /app/skill-tao-video-Youtube-ton-giao/scripts/
COPY skill-tao-video-Youtube-ton-giao/references/ /app/skill-tao-video-Youtube-ton-giao/references/
COPY skill-tao-video-Youtube-ton-giao/SKILL.md /app/skill-tao-video-Youtube-ton-giao/SKILL.md
COPY skill-tao-video-Youtube-ton-giao/requirements.txt /app/skill-tao-video-Youtube-ton-giao/requirements.txt

# Worker resolves skill scripts from here (clients/subprocess_util.py).
ENV REELO_SKILL_ROOT=/app/skill-tao-video-Youtube-ton-giao

EXPOSE 8000

# Default command = web. The worker service overrides this in compose with:
#   command: ["arq", "worker.settings.WorkerSettings"]
CMD ["uvicorn", "web.app:app", "--host", "0.0.0.0", "--port", "8000"]
