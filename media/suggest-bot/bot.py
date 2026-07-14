"""suggest-bot — digest semanal + /sugira; botões ➕/🙈 fecham o ciclo via Jellyseerr."""
import asyncio
import logging
import os
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

from telegram import (InaccessibleMessage, InlineKeyboardButton,
                      InlineKeyboardMarkup, Update)
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler,
                          ContextTypes, Defaults, filters)

import cards
import notify
import scheduling
from config import Settings
from jellyseerr import AlreadyRequested, JellyseerrClient
from mdblist import MdblistClient
from pipeline import build_digest
from state import DISMISSED, REQUESTED, SuggestionStore
from trakt import TraktClient

log = logging.getLogger("suggest-bot")

_STATUS = {"requested": "✅ Pedido", "already": "✅ Já pedido/disponível",
           "dismissed": "🙈 Dispensado"}


def status_suffix(kind: str) -> str:
    return f"\n\n{_STATUS[kind]}"


def keyboard_for(s) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("➕ Adicionar", callback_data=cards.callback_add(s)),
        InlineKeyboardButton("🙈 Dispensar", callback_data=cards.callback_dismiss(s)),
    ]])


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat()


async def send_digest(app: Application, trigger: str) -> None:
    cfg, deps = app.bot_data["cfg"], app.bot_data["deps"]
    try:
        suggestions, notes = await asyncio.to_thread(
            build_digest, deps["jelly"], deps["trakt"], deps["mdb"],
            deps["store"], cfg, _now_iso())
    except Exception:
        log.exception("digest falhou (%s)", trigger)
        await asyncio.to_thread(notify.push, cfg.ntfy_url, "suggest-bot",
                                f"Digest falhou ({trigger}) — ver logs")
        await app.bot.send_message(cfg.telegram_chat_id,
                                   "❌ Não consegui montar as sugestões (Jellyseerr fora?).")
        return
    if not suggestions:
        await app.bot.send_message(cfg.telegram_chat_id, "📭 Sem sugestões novas desta vez.")
    else:
        header = f"📬 Sugestões ({len(suggestions)})"
        if notes:
            header += "\n⚠️ " + "; ".join(notes)
        await app.bot.send_message(cfg.telegram_chat_id, header)
        for s in suggestions:
            url = cards.poster_url(s)
            kwargs = dict(caption=cards.caption(s), parse_mode="HTML",
                          reply_markup=keyboard_for(s))
            try:
                if url:
                    try:
                        await app.bot.send_photo(cfg.telegram_chat_id, url, **kwargs)
                        continue
                    except Exception:
                        log.exception("poster falhou (%s/%s) — caindo para texto",
                                      s.title, s.tmdb_id)
                await app.bot.send_message(cfg.telegram_chat_id, kwargs.pop("caption"),
                                           parse_mode="HTML", reply_markup=keyboard_for(s))
            except Exception:
                # um card ruim (poster inacessível, rate-limit, etc.) não pode abortar
                # o resto do digest nem deixar de marcar o horário de envio.
                log.exception("card falhou (%s/%s) — pulando", s.title, s.tmdb_id)
    deps["store"].set_last_digest_at(_now_iso())
    log.info("digest enviado (%s): %d sugestões", trigger, len(suggestions))


async def on_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 Sou o suggest-bot. Mando sugestões toda semana; /sugira pede na hora.")


async def on_sugira(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("🔎 Buscando sugestões…")
    await send_digest(context.application, "sob demanda")


async def _edit_status(query, kind: str) -> None:
    msg = query.message
    if isinstance(msg, InaccessibleMessage):
        # Cartões com mais de ~48h chegam como InaccessibleMessage (só chat/message_id/date,
        # sem caption/edit_caption) — editar é impossível; a ação já foi feita e o feedback
        # do q.answer() já foi dado, então só desistimos silenciosamente da edição visual.
        return
    if msg.caption is not None:
        await msg.edit_caption(msg.caption + status_suffix(kind), reply_markup=None)
    else:
        await msg.edit_text(msg.text + status_suffix(kind), reply_markup=None)


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg, deps = context.bot_data["cfg"], context.bot_data["deps"]
    q = update.callback_query
    # .chat.id existe tanto em Message quanto em InaccessibleMessage; .chat_id não.
    if q.message is None or q.message.chat.id != cfg.telegram_chat_id:
        await q.answer()
        return
    try:
        action, media_type, tmdb_id = cards.parse_callback(q.data)
    except ValueError:
        await q.answer("Botão inválido")
        return
    if action == "add":
        try:
            await asyncio.to_thread(deps["jelly"].request, media_type, tmdb_id)
            deps["store"].mark(media_type, tmdb_id, REQUESTED, _now_iso())
            await q.answer("Pedido!")
            await _edit_status(q, "requested")
        except AlreadyRequested:
            deps["store"].mark(media_type, tmdb_id, REQUESTED, _now_iso())
            await q.answer("Já estava pedido/disponível")
            await _edit_status(q, "already")
        except Exception:
            log.exception("request %s/%s falhou", media_type, tmdb_id)
            await q.answer("❌ Falhou — tenta de novo", show_alert=True)  # mantém botões
    else:
        deps["store"].mark(media_type, tmdb_id, DISMISSED, _now_iso())
        await q.answer("Dispensado")
        await _edit_status(q, "dismissed")


async def _weekly_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    await send_digest(context.application, "semanal")


async def _post_init(app: Application) -> None:
    cfg, deps = app.bot_data["cfg"], app.bot_data["deps"]
    last = deps["store"].last_digest_at()
    last_dt = datetime.fromisoformat(last) if last else None
    if scheduling.should_catch_up(datetime.now().astimezone(), last_dt,
                                  cfg.digest_weekday, cfg.digest_hour,
                                  cfg.catchup_grace_days):
        log.info("janela do digest perdida — catch-up no boot")
        try:
            await send_digest(app, "catch-up")
        except Exception:
            # digest falho no boot nunca pode derrubar o processo (post_init do PTB).
            log.exception("catch-up do digest falhou no boot — seguindo sem travar o processo")


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    cfg = Settings.from_env()
    tz = ZoneInfo(os.environ.get("TZ", "America/Cuiaba"))
    deps = {
        "jelly": JellyseerrClient(cfg.jellyseerr_url, cfg.jellyseerr_key),
        "trakt": TraktClient(cfg.trakt_client_id),
        "mdb": MdblistClient(cfg.mdblist_key),
        "store": SuggestionStore(os.path.join(cfg.state_dir, "state.db")),
    }
    app = (Application.builder().token(cfg.telegram_token)
           .defaults(Defaults(tzinfo=tz)).post_init(_post_init).build())
    app.bot_data.update({"cfg": cfg, "deps": deps})
    only_owner = filters.Chat(chat_id=cfg.telegram_chat_id)
    app.add_handler(CommandHandler("start", on_start, filters=only_owner))
    app.add_handler(CommandHandler("sugira", on_sugira, filters=only_owner))
    app.add_handler(CallbackQueryHandler(on_button))
    # PTB run_daily's `days` usa 0=domingo..6=sábado (verificado em _jobqueue.py, PTB 22.8);
    # nossa config usa 0=segunda (convenção datetime.weekday()) — converter.
    ptb_days = (cfg.digest_weekday + 1) % 7
    app.job_queue.run_daily(_weekly_job, time=dtime(hour=cfg.digest_hour, tzinfo=tz),
                            days=(ptb_days,))
    log.info("suggest-bot no ar — digest weekday=%d %dh", cfg.digest_weekday, cfg.digest_hour)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
