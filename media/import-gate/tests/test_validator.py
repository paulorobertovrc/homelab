import os
import subprocess
import pytest
from types import SimpleNamespace
from validator import validate


def _settings(**over):
    base = dict(lang_prob_threshold=0.7, sample_windows=2, sample_seconds=3,
                skip_intro_fraction=0.1)
    base.update(over)
    return SimpleNamespace(**base)


@pytest.fixture
def eng_clip(tmp_path):
    out = os.path.join(tmp_path, "clip.mkv")
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=35:size=128x72:rate=5",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=35",
         "-metadata:s:a:0", "language=eng", "-shortest", out],
        check=True, capture_output=True,
    )
    return out


def test_pass_when_detected_matches_original(eng_clip):
    v = validate(eng_clip, "English", 1, _settings(), lambda p: ("en", 0.95))
    assert v.ok is True and v.reason == "ok"


def test_reject_confident_mismatch(eng_clip):
    v = validate(eng_clip, "English", 1, _settings(), lambda p: ("ru", 0.95))
    assert v.ok is False and v.reason == "wrong-language"
    assert "ru" in v.detail


def test_low_confidence_passes(eng_clip):
    # detected differs but below threshold -> do not destroy a good file
    v = validate(eng_clip, "English", 1, _settings(), lambda p: ("ru", 0.4))
    assert v.ok is True


def test_corrupt_file_rejected_without_whisper(tmp_path):
    bad = os.path.join(tmp_path, "bad.mkv")
    open(bad, "wb").write(b"not a video")

    def boom(_):
        raise AssertionError("whisper must not run on a corrupt file")

    v = validate(bad, "English", 1, _settings(), boom)
    assert v.ok is False and v.reason == "corrupt"


def test_tied_confident_votes_passes(eng_clip):
    # Two windows, each confident, but for two DIFFERENT non-original languages
    # in a 1-1 tie -> must NOT reject (ties/ambiguous confident evidence -> pass).
    calls = iter([("ru", 0.9), ("fr", 0.9)])
    v = validate(eng_clip, "English", 1, _settings(), lambda p: next(calls))
    assert v.ok is True


def test_transcribe_error_sets_errored_not_reject(eng_clip):
    # EVERY sample fails whisper (e.g. a broken model) -> errored gate, loud
    # fail-open, no quarantine. Total failure must stay observable.
    def boom(_):
        raise RuntimeError("model exploded")

    v = validate(eng_clip, "English", 1, _settings(), boom)
    assert v.errored is True and v.ok is True  # errored gate does not quarantine


def test_partial_transcribe_failure_uses_good_votes(eng_clip):
    # Real bug: a short stream's tail window yields a 0-sample clip and whisper
    # raises `max() iterable argument is empty` on it. That single bad sample must
    # NOT abort the whole file (fail-open); the remaining good samples must decide.
    seq = iter([ValueError("max() iterable argument is empty"), ("en", 0.95)])

    def one_bad_clip(_):
        nxt = next(seq)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    v = validate(eng_clip, "English", 1, _settings(sample_windows=2), one_bad_clip)
    assert v.errored is False        # one unusable sample is not a gate failure
    assert v.ok is True and v.reason == "ok"


# --- bilingual originals ------------------------------------------------------
# Measured 2026-07-26 on Agent Kim Reactivated S01E04, after the gate rejected
# THREE releases of it and then gave up on a fourth. Profiling the Korean track of
# all four at 12 windows gave the identical row (same Netflix master, three
# rippers): (en) ko (en) ko zh zh ko ko ko ko (en) en -- 9 confident detections,
# 6 of them Korean. The track is genuinely Korean; the episode simply has English
# scenes. Plurality-over-3-windows cannot tell that apart from a dub.
#
# The negative control is the user's own hand-quarantined "O Negocio" (Brazilian,
# so orig=pt) carrying Russian audio: ru x11, pt=0. Real dubs score ZERO in the
# original language -- so "does this stream contain the original at all" separates
# the two with a 6-vs-0 margin, where "is the original the plurality" does not.

def _cycle(votes):
    """transcribe_fn that walks a fixed list of (code, prob), repeating the last."""
    seq = list(votes)

    def fn(_clip):
        return seq.pop(0) if len(seq) > 1 else seq[0]

    return fn


def test_bilingual_original_track_passes(eng_clip):
    """The Agent Kim S01E04 regression: majority English, but Korean IS present."""
    v = validate(eng_clip, "English", 1, _settings(sample_windows=4),
                 _cycle([("ru", 0.95), ("ru", 0.95), ("en", 0.95), ("ru", 0.95)]))
    assert v.ok is True, v.detail


def test_fully_dubbed_track_is_still_rejected(eng_clip):
    """The O Negocio regression: not one sample in the original language."""
    v = validate(eng_clip, "English", 1, _settings(sample_windows=4),
                 _cycle([("ru", 0.95)]))
    assert v.ok is False and v.reason == "wrong-language"
    assert "ru" in v.detail


def test_a_single_confident_original_sample_is_enough(eng_clip):
    """Deliberately generous. A dub scores 0 across 60 measured samples, so the
    gap between 0 and 1 is the whole signal; demanding more would re-import the
    sampling fragility this fixes."""
    v = validate(eng_clip, "English", 1, _settings(sample_windows=4),
                 _cycle([("ru", 0.95), ("ru", 0.95), ("ru", 0.95), ("en", 0.95)]))
    assert v.ok is True


def test_low_confidence_original_does_not_rescue_a_dub(eng_clip):
    """Only confident detections count as evidence of the original."""
    v = validate(eng_clip, "English", 1, _settings(sample_windows=4),
                 _cycle([("ru", 0.95), ("ru", 0.95), ("en", 0.3), ("ru", 0.95)]))
    assert v.ok is False and v.reason == "wrong-language"


def test_default_sample_windows_is_wide_enough_for_a_bilingual_episode():
    """3 windows over a 62-min episode is 90s, and on the real file the gate's
    three offsets all landed on English. The count is part of the fix."""
    from config import Settings
    import os as _os
    for k in ("SAMPLE_WINDOWS", "RADARR_API_KEY", "SONARR_API_KEY"):
        _os.environ.pop(k, None)
    _os.environ["RADARR_API_KEY"] = "x"
    _os.environ["SONARR_API_KEY"] = "x"
    assert Settings.from_env().sample_windows >= 6
