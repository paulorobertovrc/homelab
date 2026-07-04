"""Thin wrappers over ffprobe/ffmpeg: integrity + stream info, and audio sampling."""
import json
import os
import subprocess
from dataclasses import dataclass


class ProbeError(Exception):
    pass


@dataclass
class ProbeResult:
    has_video: bool
    has_audio: bool
    duration_sec: float
    audio_langs: list  # list[str | None], one per audio stream


def probe(path: str) -> ProbeResult:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", "-show_streams", path],
            check=True, capture_output=True, text=True,
        ).stdout
    except subprocess.CalledProcessError as e:
        raise ProbeError(f"ffprobe failed for {path}: {e.stderr}") from e
    data = json.loads(out)
    streams = data.get("streams", [])
    video = [s for s in streams if s.get("codec_type") == "video"]
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    try:
        duration = float(data.get("format", {}).get("duration", 0.0))
    except (TypeError, ValueError):
        duration = 0.0
    audio_langs = [s.get("tags", {}).get("language") for s in audio]
    return ProbeResult(
        has_video=bool(video),
        has_audio=bool(audio),
        duration_sec=duration,
        audio_langs=audio_langs,
    )


def extract_windows(path, stream_index, out_dir, windows, seconds,
                    skip_fraction, duration_sec):
    """Extract `windows` wav clips of `seconds` each, evenly spaced through the
    middle of the file, skipping the first `skip_fraction` of runtime."""
    start = duration_sec * skip_fraction
    usable = max(duration_sec - start - seconds, 0)
    clips = []
    for i in range(windows):
        offset = start + (usable * i / max(windows - 1, 1) if windows > 1 else usable / 2)
        clip = os.path.join(out_dir, f"win_{i}.wav")
        subprocess.run(
            ["ffmpeg", "-y", "-ss", str(offset), "-t", str(seconds),
             "-i", path, "-map", f"0:a:{stream_index}",
             "-ac", "1", "-ar", "16000", clip],
            check=True, capture_output=True,
        )
        clips.append(clip)
    return clips
