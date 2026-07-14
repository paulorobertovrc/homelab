"""ntfy push (best-effort; never raises into the caller) — mirrors import-gate/notify.py."""
import requests


def push(ntfy_url: str, title: str, message: str) -> None:
    try:
        requests.post(
            ntfy_url,
            data=message.encode("utf-8"),
            headers={"Title": title, "Tags": "robot"},
            timeout=10,
        )
    except Exception:
        pass
