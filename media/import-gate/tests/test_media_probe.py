import os
import subprocess
import pytest
from media_probe import probe, extract_windows, ProbeError


@pytest.fixture
def sine_video(tmp_path):
    """A 20s clip: 1 video stream + 1 audio stream (440Hz tone)."""
    out = os.path.join(tmp_path, "clip.mkv")
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=20:size=128x72:rate=5",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=20",
         "-metadata:s:a:0", "language=eng", "-shortest", out],
        check=True, capture_output=True,
    )
    return out


def test_probe_detects_streams(sine_video):
    r = probe(sine_video)
    assert r.has_video is True
    assert r.has_audio is True
    assert r.duration_sec == pytest.approx(20, abs=1)
    assert r.audio_langs == ["eng"]


def test_probe_unreadable_raises(tmp_path):
    bad = os.path.join(tmp_path, "nope.mkv")
    open(bad, "wb").write(b"not a video")
    with pytest.raises(ProbeError):
        probe(bad)


def test_extract_windows_returns_clips(sine_video):
    out_dir = os.path.dirname(sine_video)
    clips = extract_windows(sine_video, 0, out_dir, windows=2, seconds=3,
                            skip_fraction=0.1, duration_sec=20)
    assert len(clips) == 2
    for c in clips:
        assert os.path.getsize(c) > 0
