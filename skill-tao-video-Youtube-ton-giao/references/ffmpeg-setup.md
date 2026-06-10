# FFmpeg Setup

FFmpeg is a free, open-source command-line tool. It runs entirely on the user's machine — **no API key, no account, no internet required.**

The skill's `scripts/merge_video.py` shells out to the `ffmpeg` command. It must be installed and on the system PATH.

## Install

### Windows
```powershell
winget install ffmpeg
```
After install, open a **new** PowerShell window (PATH is set in the new shell).

Alternative without winget: download the static build from [gyan.dev/ffmpeg/builds](https://www.gyan.dev/ffmpeg/builds/), extract somewhere like `C:\ffmpeg`, then add `C:\ffmpeg\bin` to PATH via System Properties → Environment Variables.

### macOS
```bash
brew install ffmpeg
```

### Linux (Debian / Ubuntu)
```bash
sudo apt update && sudo apt install ffmpeg
```

### Linux (other distros)
- Fedora: `sudo dnf install ffmpeg` (enable RPM Fusion first)
- Arch: `sudo pacman -S ffmpeg`

## Verify

```bash
ffmpeg -version
```

Should print something like `ffmpeg version 6.1.x ...`. If the command isn't found, FFmpeg isn't on PATH — fix that before running the merge step.

## What the merge script does with FFmpeg

Image timing is auto-calculated: `merge_video.py` reads `script.md`, splits by `===` markers, and assigns each section a fraction of the audio duration equal to its word-count fraction. Then for each image:

1. Loops the still image for its allotted duration
2. Builds a 1920x1080 frame:
   - **Background**: same image, scaled to fill, Gaussian-blurred (sigma=30), darkened (-0.25 brightness) — only visible when the source aspect is narrower than 16:9
   - **Foreground**: same image, scaled to 1080px height, centered horizontally
3. Renders to a temp MP4 clip (H.264, CRF 20, yuv420p). Image is fully static — no zoom or pan.

Then the clips are chained:
1. Each adjacent pair joined with an `xfade` crossfade transition of 0.6s
2. The voice MP3 is muxed in as the audio track
3. Final output: H.264 + AAC, 1920x1080, 30 fps

## Common errors

| Error | Fix |
|---|---|
| `ffmpeg: command not found` | FFmpeg isn't installed or not on PATH. Reinstall, then open a new terminal. |
| `Error opening input file <image>` | The image file is missing or has a non-PNG/JPEG format the FFmpeg build doesn't recognize. Most prebuilt FFmpegs handle PNG/JPEG/WebP fine. |
| `Conversion failed!` (during xfade) | Usually a duration mismatch — a clip is shorter than expected. Check that all clips rendered successfully in the temp folder. |
| Output video shorter or longer than audio | Can't happen with auto-calculated timing — section durations sum to exactly the audio length by construction. If it does, it's a bug. |

## Performance

A 5-minute video with 5 images renders in roughly 30-90 seconds on modern hardware. Speed scales with image count and CPU. For faster (lower quality) preview, edit `merge_video.py` and change `-preset medium` to `-preset ultrafast` and `-crf 20` to `-crf 28`.

## Notes on the FFmpeg "API" misconception

FFmpeg is not a cloud service and has no API key. Some confusion arises because there are paid video APIs like [shotstack.io](https://shotstack.io) and [json2video.com](https://json2video.com) — these *do* use FFmpeg under the hood, but charge for hosting it on their servers. For this skill, running FFmpeg locally is free, faster (no upload/download), and gives identical output quality.
