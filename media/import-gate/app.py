"""Flask webhook receiver + self-heal orchestration."""
import logging
import os
import shutil
from flask import Flask, request, jsonify

logger = logging.getLogger(__name__)


def _title_key(kind, media_id):
    return f"{kind}:{media_id}"


def create_app(settings, radarr, sonarr, store, validate_fn, notify_fn):
    app = Flask(__name__)
    seen_download_ids = set()  # idempotency within process lifetime

    @app.get("/health")
    def health():
        return jsonify(status="ok")

    @app.post("/webhook")
    def webhook():
        payload = request.get_json(force=True, silent=True) or {}
        event = payload.get("eventType")
        if event == "Test":
            return jsonify(status="test-ok")
        if event != "Download":
            return jsonify(status="ignored", event=event)

        is_radarr = "movie" in payload
        arr = radarr if is_radarr else sonarr
        kind = "radarr" if is_radarr else "sonarr"

        if is_radarr:
            media = payload["movie"]
            media_file = payload["movieFile"]
            media_id = media["id"]
            title = media.get("title", "?")
            orig_lang = media.get("originalLanguage", {}).get("name", "")
            runtime = media.get("runtime")
            file_id = media_file["id"]
            delete_file = arr.delete_moviefile
        else:
            media = payload["series"]
            media_file = payload["episodeFile"]
            media_id = media["id"]
            title = media.get("title", "?")
            orig_lang = media.get("originalLanguage", {}).get("name", "")
            runtime = None
            file_id = media_file["id"]
            delete_file = arr.delete_episodefile

        download_id = payload.get("downloadId")
        if download_id and download_id in seen_download_ids:
            return jsonify(status="duplicate")
        if download_id:
            seen_download_ids.add(download_id)

        path = media_file["path"]
        verdict = validate_fn(path=path, original_language_name=orig_lang,
                              expected_runtime_min=runtime)

        if verdict.errored:
            msg = f"{title}: importado sem validação ({verdict.detail})"
            notify_fn(settings.ntfy_url, "Import-gate indisponível", "warning", 3, msg)
            logger.warning(msg)
            return jsonify(status="errored-passed")

        if verdict.ok:
            return jsonify(status="passed")

        # --- reject: loop guard, quarantine, self-heal ---
        key = _title_key(kind, media_id)
        attempts = store.get(key)
        if attempts >= settings.max_attempts:
            msg = (f"{title}: {settings.max_attempts} tentativas sem faixa original. "
                   f"Intervenção manual necessária.")
            notify_fn(settings.ntfy_url, "⚠️ Import-gate desistiu", "no_entry", 4, msg)
            logger.error(msg)
            return jsonify(status="gave-up")

        try:
            _quarantine(path, settings, title, verdict.reason, attempts)
            delete_file(file_id)
            if download_id:
                hid = arr.find_grab_history_id(download_id)
                if hid is not None:
                    arr.mark_failed(hid)
        except Exception as e:
            if download_id:
                seen_download_ids.discard(download_id)
            msg = f"{title}: quarentenado, mas self-heal falhou: {e}"
            notify_fn(settings.ntfy_url, "Import-gate erro no self-heal", "warning", 4, msg)
            logger.error(msg)
            return jsonify(status="quarantined-selfheal-failed")

        n = store.increment(key)
        msg = f"{title}: {verdict.detail}. Tentativa {n}. Re-busca disparada."
        notify_fn(settings.ntfy_url, "🔒 Quarentena", "lock", 3, msg)
        logger.warning(msg)
        return jsonify(status="quarantined", attempt=n)

    def _quarantine(path, settings, title, reason, attempt_number):
        dest_dir = os.path.join(settings.quarantine_root, f"{title} ({reason})")
        os.makedirs(dest_dir, exist_ok=True)
        dest_name = f"attempt-{attempt_number + 1}-{os.path.basename(path)}"
        shutil.copy2(path, os.path.join(dest_dir, dest_name))

    return app


if __name__ == "__main__":  # production entrypoint
    from config import Settings
    from arr_client import ArrClient
    from state import AttemptStore
    from validator import validate as _validate
    from notify import push as _push
    from faster_whisper import WhisperModel

    s = Settings.from_env()
    model = WhisperModel(s.whisper_model, device="cpu", compute_type="int8")

    def transcribe_fn(clip_path):
        _segs, info = model.transcribe(clip_path)
        return info.language, info.language_probability

    def validate_fn(path, original_language_name, expected_runtime_min):
        return _validate(path, original_language_name, expected_runtime_min, s, transcribe_fn)

    application = create_app(
        s,
        ArrClient(s.radarr_url, s.radarr_key, "radarr"),
        ArrClient(s.sonarr_url, s.sonarr_key, "sonarr"),
        AttemptStore(os.path.join(s.state_dir, "attempts.db")),
        validate_fn, _push,
    )
    application.run(host="0.0.0.0", port=8080)
