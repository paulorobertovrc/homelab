from datetime import datetime

from scheduling import last_slot, should_catch_up

# 2026-07-14 é terça (weekday 1). Digest: sexta (4) 18h.
FRI, HR, GRACE = 4, 18, 3


def test_last_slot_goes_back_to_friday():
    now = datetime(2026, 7, 14, 10, 0)  # terça
    assert last_slot(now, FRI, HR) == datetime(2026, 7, 10, 18, 0)


def test_last_slot_same_day_before_hour_goes_to_prev_week():
    now = datetime(2026, 7, 10, 17, 0)  # sexta 17h, antes das 18h
    assert last_slot(now, FRI, HR) == datetime(2026, 7, 3, 18, 0)


def test_last_slot_same_day_after_hour_is_today():
    now = datetime(2026, 7, 10, 19, 0)
    assert last_slot(now, FRI, HR) == datetime(2026, 7, 10, 18, 0)


def test_catch_up_when_missed_within_grace():
    now = datetime(2026, 7, 12, 9, 0)  # domingo; slot sexta 18h há <3d
    assert should_catch_up(now, datetime(2026, 7, 3, 18, 5), FRI, HR, GRACE) is True


def test_no_catch_up_when_already_sent():
    now = datetime(2026, 7, 12, 9, 0)
    assert should_catch_up(now, datetime(2026, 7, 10, 18, 5), FRI, HR, GRACE) is False


def test_no_catch_up_when_too_old():
    now = datetime(2026, 7, 14, 10, 0)  # terça; slot sexta 18h há >3d
    assert should_catch_up(now, datetime(2026, 7, 3, 18, 5), FRI, HR, GRACE) is False


def test_never_sent_within_grace_catches_up():
    now = datetime(2026, 7, 12, 9, 0)
    assert should_catch_up(now, None, FRI, HR, GRACE) is True
