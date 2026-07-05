"""Decide pass/reject for one imported file. Integrity first (cheap), then whisper."""
import logging
import tempfile
from collections import Counter
from dataclasses import dataclass

from languages import to_code
import media_probe

logger = logging.getLogger(__name__)


@dataclass
class Verdict:
    ok: bool
    reason: str      # "ok" | "corrupt" | "no-audio" | "wrong-language"
    detail: str
    errored: bool = False


def validate(path, original_language_name, expected_runtime_min, settings, transcribe_fn):
    # 1. Integrity (cheap, no whisper).
    try:
        result = media_probe.probe(path)
    except media_probe.ProbeError:
        return Verdict(False, "corrupt", "ffprobe could not read the file")

    if not result.has_video or result.duration_sec <= 0:
        return Verdict(False, "corrupt", "missing video stream or zero duration")
    if not result.has_audio:
        return Verdict(False, "no-audio", "no audio stream")
    if expected_runtime_min:
        floor = expected_runtime_min * 60 * 0.5  # >50% of expected runtime
        if result.duration_sec < floor:
            return Verdict(False, "corrupt",
                           f"duration {int(result.duration_sec)}s < floor {int(floor)}s")

    orig_code = to_code(original_language_name)
    if orig_code is None:
        # Unknown original language -> can't judge; pass (don't destroy).
        return Verdict(True, "ok", f"original language '{original_language_name}' not mappable")

    # 2. Whisper on each audio stream; pass as soon as one stream is confidently original.
    confident_mismatch = None
    any_sample_ok = False
    try:
        for stream_index, _ in enumerate(result.audio_langs):
            with tempfile.TemporaryDirectory() as tmp:
                clips = media_probe.extract_windows(
                    path, stream_index, tmp, settings.sample_windows,
                    settings.sample_seconds, settings.skip_intro_fraction,
                    result.duration_sec,
                )
                votes = []
                for clip in clips:
                    try:
                        code, prob = transcribe_fn(clip)
                    except Exception as e:
                        # One unusable sample must not blind the gate to the rest.
                        # A stream shorter than the container yields a 0-sample tail
                        # clip, on which faster-whisper raises "max() ... is empty".
                        # Skip that sample; log it so systematic failures stay visible.
                        logger.warning("whisper failed on a sample of %s; skipping it: %s", path, e)
                        continue
                    any_sample_ok = True
                    if prob >= settings.lang_prob_threshold:
                        votes.append(code)
                if votes:
                    counts = Counter(votes).most_common()
                    winner, top_count = counts[0]
                    tied = len(counts) > 1 and counts[1][1] == top_count
                    if not tied:
                        if winner == orig_code:
                            return Verdict(True, "ok", f"orig={orig_code}, stream {stream_index} matches")
                        # Confident, but for a different language than expected.
                        confident_mismatch = winner
    except Exception as e:  # extraction/other failure -> gate error, never quarantine
        return Verdict(True, "ok", f"gate error: {e}", errored=True)

    if confident_mismatch:
        # At least one stream was confidently NOT the original language.
        return Verdict(False, "wrong-language",
                       f"orig={orig_code}, detected={confident_mismatch}")

    if not any_sample_ok:
        # Every whisper call failed (e.g. a broken model) -> systematic gate
        # failure. Loud fail-open (never quarantine), visible via the errored path.
        return Verdict(True, "ok", "gate error: no audio sample could be analysed", errored=True)

    # No stream produced a confident detection either way -> can't judge; pass (don't destroy).
    return Verdict(True, "ok", f"orig={orig_code}, no confident detection")
