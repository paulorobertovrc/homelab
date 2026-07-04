"""ntfy push (best-effort; never raises into the caller)."""
import requests


def push(ntfy_url: str, title: str, tags: str, priority: int, message: str) -> None:
    try:
        requests.post(
            ntfy_url,
            data=message.encode("utf-8"),
            headers={"Title": title, "Tags": tags, "Priority": str(priority)},
            timeout=10,
        )
    except Exception:
        pass
