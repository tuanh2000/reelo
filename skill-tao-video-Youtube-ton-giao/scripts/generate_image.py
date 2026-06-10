#!/usr/bin/env python3
"""Generate an image via kie.ai's gpt-image-2-text-to-image model.

Reads KIE_API_KEY from .env at the skill root.
Submits an async task via /api/v1/jobs/createTask, polls /api/v1/jobs/recordInfo
until the state is "success", downloads the resulting PNG.
Prints metadata JSON to stdout.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

SKILL_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(SKILL_ROOT / ".env")

KIE_BASE = "https://api.kie.ai/api/v1"
MODEL = "gpt-image-2-text-to-image"
MAX_ATTEMPTS = 3
RETRY_WAIT_BASE = 3

# kie.ai's gpt-image-2 supports these aspect-ratio strings (not pixel dimensions):
VALID_SIZES = {"1:1", "3:2", "2:3", "4:3", "3:4", "16:9", "9:16"}

SUCCESS_STATES = {"success", "succeed", "succeeded", "completed", "complete", "done"}
FAILURE_STATES = {"failed", "fail", "error", "generate_failed"}


def submit_task(prompt: str, size: str, api_key: str) -> str:
    url = f"{KIE_BASE}/jobs/createTask"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": MODEL,
        "input": {"prompt": prompt, "size": size},
    }
    last_err = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = requests.post(url, json=body, headers=headers, timeout=60)
            if response.status_code == 200:
                payload = response.json()
                if payload.get("code") in (200, 0):
                    task_id = payload.get("data", {}).get("taskId")
                    if task_id:
                        return task_id
                    last_err = f"no taskId in response: {payload}"
                else:
                    last_err = f"kie.ai error: {payload}"
                    # Permanent errors (e.g. 422 size error) shouldn't retry
                    if payload.get("code") == 422:
                        raise SystemExit(last_err)
            else:
                last_err = f"HTTP {response.status_code}: {response.text[:300]}"
        except requests.RequestException as e:
            last_err = str(e)
        if attempt < MAX_ATTEMPTS:
            wait = RETRY_WAIT_BASE * attempt
            print(
                f"kie.ai submit attempt {attempt}/{MAX_ATTEMPTS} failed: {last_err}. "
                f"Retrying in {wait}s...",
                file=sys.stderr,
            )
            time.sleep(wait)
    raise SystemExit(f"kie.ai create-task failed after {MAX_ATTEMPTS} attempts: {last_err}")


def extract_image_url(record_data: dict) -> str | None:
    """Pull an image URL out of the jobs/recordInfo response when state == success."""
    result_json = record_data.get("resultJson") or ""
    if not result_json:
        return None
    if isinstance(result_json, str):
        try:
            parsed = json.loads(result_json)
        except json.JSONDecodeError:
            return result_json if result_json.startswith("http") else None
    else:
        parsed = result_json

    # Walk common shapes
    if isinstance(parsed, str) and parsed.startswith("http"):
        return parsed
    if isinstance(parsed, list) and parsed:
        first = parsed[0]
        return first if isinstance(first, str) else first.get("url") if isinstance(first, dict) else None
    if isinstance(parsed, dict):
        for key in ("resultUrls", "urls", "images", "imageUrls", "output", "image_url", "url"):
            val = parsed.get(key)
            if isinstance(val, list) and val:
                first = val[0]
                if isinstance(first, str):
                    return first
                if isinstance(first, dict):
                    return first.get("url") or first.get("imageUrl") or first.get("imageURL")
            if isinstance(val, str) and val.startswith("http"):
                return val
    return None


def poll_task(task_id: str, api_key: str, max_wait: int, poll_interval: int) -> str:
    url = f"{KIE_BASE}/jobs/recordInfo"
    headers = {"Authorization": f"Bearer {api_key}"}
    elapsed = 0
    last_state = None
    while elapsed < max_wait:
        time.sleep(poll_interval)
        elapsed += poll_interval
        response = requests.get(url, params={"taskId": task_id}, headers=headers, timeout=30)
        if response.status_code != 200:
            continue
        data = response.json().get("data", {}) or {}
        state = (data.get("state") or "").lower()
        last_state = state
        if state in SUCCESS_STATES:
            img_url = extract_image_url(data)
            if img_url:
                return img_url
            raise SystemExit(
                f"kie.ai reports success but no image URL found in response: {data}"
            )
        if state in FAILURE_STATES:
            fail_msg = data.get("failMsg") or data.get("failCode") or "unknown"
            raise SystemExit(f"kie.ai task failed: {fail_msg}\nFull: {data}")
    raise SystemExit(
        f"kie.ai task {task_id} timed out after {max_wait}s (last state: {last_state})"
    )


def download_image(url: str, output_path: Path) -> int:
    last_err = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = requests.get(url, timeout=120)
            if response.status_code == 200 and len(response.content) > 0:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(response.content)
                return output_path.stat().st_size
            last_err = f"HTTP {response.status_code}, body size {len(response.content)}"
        except requests.RequestException as e:
            last_err = str(e)
        if attempt < MAX_ATTEMPTS:
            wait = RETRY_WAIT_BASE * attempt
            print(
                f"Image download attempt {attempt}/{MAX_ATTEMPTS} failed: {last_err}. "
                f"Retrying in {wait}s...",
                file=sys.stderr,
            )
            time.sleep(wait)
    raise SystemExit(f"Image download failed after {MAX_ATTEMPTS} attempts: {last_err}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt-file", required=True, help="Path to UTF-8 text file containing the image prompt")
    parser.add_argument("--output", required=True, help="Output PNG path")
    parser.add_argument(
        "--size",
        default="16:9",
        help=(
            "Aspect ratio for the generated image. kie.ai's gpt-image-2 accepts "
            "ratio strings, NOT pixel dimensions. Valid: 16:9 (default, YouTube native), "
            "9:16 (Shorts), 4:3, 3:4, 3:2, 2:3, 1:1."
        ),
    )
    parser.add_argument("--max-wait", type=int, default=300, help="Max seconds to wait")
    parser.add_argument("--poll-interval", type=int, default=5)
    args = parser.parse_args()

    api_key = os.environ.get("KIE_API_KEY")
    if not api_key:
        raise SystemExit(
            "KIE_API_KEY not set. Add it to .env in the skill root.\n"
            f"Expected at: {SKILL_ROOT / '.env'}"
        )

    if args.size not in VALID_SIZES:
        raise SystemExit(
            f"Invalid --size {args.size!r}. Use an aspect ratio string: {sorted(VALID_SIZES)}"
        )

    prompt = Path(args.prompt_file).read_text(encoding="utf-8").strip()
    if not prompt:
        raise SystemExit(f"Prompt file is empty: {args.prompt_file}")

    start_time = time.time()
    task_id = submit_task(prompt, args.size, api_key)
    image_url = poll_task(task_id, api_key, args.max_wait, args.poll_interval)
    file_size = download_image(image_url, Path(args.output))
    elapsed = round(time.time() - start_time, 1)

    result = {
        "output_path": str(args.output),
        "task_id": task_id,
        "model": MODEL,
        "size": args.size,
        "source_url": image_url,
        "file_size_bytes": file_size,
        "elapsed_seconds": elapsed,
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
