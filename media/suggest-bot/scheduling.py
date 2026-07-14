"""Catch-up do digest semanal: se a stack estava desligada na janela, dispara no boot
se a janela perdida tem menos de grace_days; mais velha que isso, espera o próximo ciclo."""
from datetime import datetime, timedelta


def last_slot(now: datetime, weekday: int, hour: int) -> datetime:
    """O horário agendado (weekday/hour semanal) mais recente <= now."""
    slot = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    slot -= timedelta(days=(now.weekday() - weekday) % 7)
    if slot > now:
        slot -= timedelta(days=7)
    return slot


def should_catch_up(now: datetime, last_sent: datetime | None,
                    weekday: int, hour: int, grace_days: int) -> bool:
    slot = last_slot(now, weekday, hour)
    if last_sent is not None and last_sent >= slot:
        return False
    return (now - slot) <= timedelta(days=grace_days)
