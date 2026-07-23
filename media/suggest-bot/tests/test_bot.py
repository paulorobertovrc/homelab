import asyncio
from types import SimpleNamespace

from telegram import Chat, InaccessibleMessage

import bot as bot_mod
from bot import keyboard_for, on_button, status_suffix
from pipeline import Suggestion
from state import REQUESTED


def _s():
    return Suggestion(media_type="movie", tmdb_id=550, title="F", year="1999",
                      overview="", poster_path=None, source="trending", tmdb_score=8.0)


def test_keyboard_has_add_and_dismiss():
    kb = keyboard_for(_s())
    row = kb.inline_keyboard[0]
    assert row[0].callback_data == "add:movie:550"
    assert row[1].callback_data == "dis:movie:550"
    assert "Adicionar" in row[0].text and "Dispensar" in row[1].text


def test_status_suffix():
    assert status_suffix("requested") == "\n\n✅ Pedido"
    assert status_suffix("already") == "\n\n✅ Já pedido/disponível"
    assert status_suffix("dismissed") == "\n\n🙈 Dispensado"


# ---- fakes compartilhadas pelos testes async abaixo (sem PTB Application real, sem rede) ----

class FakeQuery:
    def __init__(self, data, message):
        self.data = data
        self.message = message
        self.answers = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append((text, show_alert))


class FakeJelly:
    def __init__(self):
        self.requested = []

    def request(self, media_type, tmdb_id):
        self.requested.append((media_type, tmdb_id))


class FakeStore:
    def __init__(self):
        self.marked = []
        self.last_digest_calls = []

    def mark(self, media_type, tmdb_id, status, when_iso):
        self.marked.append((media_type, tmdb_id, status))

    def last_digest_at(self):
        return None

    def set_last_digest_at(self, when_iso):
        self.last_digest_calls.append(when_iso)


class FakeMessage:
    """Mensagem normal (não InaccessibleMessage) cuja edição pode falhar sob demanda."""

    def __init__(self, chat_id, text="card", fail_edit=False):
        self.chat = SimpleNamespace(id=chat_id)
        self.caption = None
        self.text = text
        self._fail_edit = fail_edit

    async def edit_text(self, *a, **kw):
        if self._fail_edit:
            raise RuntimeError("telegram recusou a edição")

    async def edit_caption(self, *a, **kw):
        if self._fail_edit:
            raise RuntimeError("telegram recusou a edição")


class FakeBot:
    def __init__(self, fail_send_photo_first=False, fail_send_message=False):
        self._fail_send_photo_first = fail_send_photo_first
        self._fail_send_message = fail_send_message
        self.photos = []
        self.messages = []

    async def send_photo(self, chat_id, url, **kw):
        self.photos.append((chat_id, url))
        if self._fail_send_photo_first and len(self.photos) == 1:
            raise RuntimeError("telegram não conseguiu buscar o poster")

    async def send_message(self, chat_id, text, **kw):
        if self._fail_send_message:
            raise RuntimeError("telegram fora do ar")
        self.messages.append((chat_id, text))


def _cfg(**over):
    base = dict(telegram_chat_id=123, ntfy_url="http://ntfy.invalid/topic",
               digest_weekday=0, digest_hour=0, catchup_grace_days=7)
    base.update(over)
    return SimpleNamespace(**base)


def test_on_button_inaccessible_message_still_requests():
    chat = Chat(id=123, type="private")
    msg = InaccessibleMessage(chat=chat, message_id=42)  # cartão com >48h (PTB 22.8)
    q = FakeQuery(data="add:movie:550", message=msg)
    jelly, store = FakeJelly(), FakeStore()
    update = SimpleNamespace(callback_query=q)
    context = SimpleNamespace(
        bot_data={"cfg": _cfg(), "deps": {"jelly": jelly, "trakt": None, "mdb": None,
                                          "store": store}})

    asyncio.run(on_button(update, context))

    assert jelly.requested == [("movie", 550)]
    assert ("movie", 550, REQUESTED) in store.marked
    assert q.answers == [("Pedido!", False)]  # nenhuma segunda resposta -> nunca tentou editar


def test_on_button_add_success_survives_edit_failure():
    # Regressão: o request/mark já tinham sucesso; se só a edição visual do
    # cartão falhar depois, o bot não pode reportar "❌ Falhou" (mentira — o
    # pedido foi feito) nem chamar q.answer() de novo numa query já respondida.
    msg = FakeMessage(chat_id=123, fail_edit=True)
    q = FakeQuery(data="add:movie:550", message=msg)
    jelly, store = FakeJelly(), FakeStore()
    update = SimpleNamespace(callback_query=q)
    context = SimpleNamespace(
        bot_data={"cfg": _cfg(), "deps": {"jelly": jelly, "trakt": None, "mdb": None,
                                          "store": store}})

    asyncio.run(on_button(update, context))

    assert jelly.requested == [("movie", 550)]
    assert ("movie", 550, REQUESTED) in store.marked
    assert q.answers == [("Pedido!", False)]  # só uma resposta, apesar do edit falhar


def test_send_digest_continues_after_card_failure():
    suggestions = [
        Suggestion(media_type="movie", tmdb_id=1, title="A", year="2020", overview="",
                  poster_path="/a.jpg", source="trending", tmdb_score=7.0),
        Suggestion(media_type="movie", tmdb_id=2, title="B", year="2021", overview="",
                  poster_path="/b.jpg", source="trending", tmdb_score=8.0),
    ]

    def fake_build_digest(*a, **kw):
        return suggestions, []

    orig_build_digest = bot_mod.build_digest
    bot_mod.build_digest = fake_build_digest
    try:
        store = FakeStore()
        fake_bot = FakeBot(fail_send_photo_first=True)
        app = SimpleNamespace(bot_data={"cfg": _cfg(),
                                        "deps": {"jelly": None, "trakt": None, "mdb": None,
                                                "store": store}},
                              bot=fake_bot)
        asyncio.run(bot_mod.send_digest(app, "teste"))
    finally:
        bot_mod.build_digest = orig_build_digest

    assert len(fake_bot.photos) == 2  # as duas tentativas de poster aconteceram
    # header + fallback em texto do card 1 (o card 2 foi por foto, sem texto extra)
    assert len(fake_bot.messages) == 2
    assert store.last_digest_calls  # não abortou antes de marcar o horário


class _RecordingSleep:
    """Substitui bot._sleep nos testes: registra os delays, nunca dorme de verdade."""

    def __init__(self):
        self.calls = []

    async def __call__(self, seconds):
        self.calls.append(seconds)


def test_send_digest_retries_transient_failure_then_succeeds():
    suggestions = [Suggestion(media_type="movie", tmdb_id=1, title="A", year="2020",
                              overview="", poster_path=None, source="trending",
                              tmdb_score=7.0)]
    attempts = []

    def flaky_build_digest(*a, **kw):
        attempts.append(1)
        if len(attempts) < 3:
            raise RuntimeError("Jellyseerr flakeou (400 transitório)")
        return suggestions, []

    orig_build_digest, orig_sleep = bot_mod.build_digest, bot_mod._sleep
    fake_sleep = _RecordingSleep()
    bot_mod.build_digest = flaky_build_digest
    bot_mod._sleep = fake_sleep
    try:
        store = FakeStore()
        fake_bot = FakeBot()
        app = SimpleNamespace(bot_data={"cfg": _cfg(),
                                        "deps": {"jelly": None, "trakt": None, "mdb": None,
                                                "store": store}},
                              bot=fake_bot)
        asyncio.run(bot_mod.send_digest(app, "teste"))
    finally:
        bot_mod.build_digest, bot_mod._sleep = orig_build_digest, orig_sleep

    assert len(attempts) == 3  # 2 falhas + sucesso na 3ª
    assert len(fake_sleep.calls) == 2  # um sleep entre cada retry, nenhum após o sucesso
    assert store.last_digest_calls  # digest foi enviado e carimbado normalmente
    assert not fake_bot.messages or "❌" not in fake_bot.messages[-1][1]


def test_send_digest_exhausts_retries_and_fails_as_before():
    def always_fails(*a, **kw):
        raise RuntimeError("Jellyseerr fora de verdade")

    orig_build_digest, orig_sleep = bot_mod.build_digest, bot_mod._sleep
    orig_push = bot_mod.notify.push
    fake_sleep = _RecordingSleep()
    pushed = []
    bot_mod.build_digest = always_fails
    bot_mod._sleep = fake_sleep
    bot_mod.notify.push = lambda *a, **kw: pushed.append(a)
    try:
        store = FakeStore()
        fake_bot = FakeBot()
        app = SimpleNamespace(bot_data={"cfg": _cfg(),
                                        "deps": {"jelly": None, "trakt": None, "mdb": None,
                                                "store": store}},
                              bot=fake_bot)
        asyncio.run(bot_mod.send_digest(app, "teste"))
    finally:
        bot_mod.build_digest, bot_mod._sleep = orig_build_digest, orig_sleep
        bot_mod.notify.push = orig_push

    assert len(fake_sleep.calls) == bot_mod._DIGEST_RETRY_ATTEMPTS - 1
    assert pushed  # alerta ntfy disparado, como antes
    assert fake_bot.messages and "❌" in fake_bot.messages[-1][1]
    assert not store.last_digest_calls  # nunca carimba um digest que nunca foi enviado


def test_post_init_swallows_digest_errors():
    def boom(*a, **kw):
        raise RuntimeError("pipeline explodiu")

    orig_build_digest, orig_sleep = bot_mod.build_digest, bot_mod._sleep
    orig_push = bot_mod.notify.push
    bot_mod.build_digest = boom
    bot_mod._sleep = _RecordingSleep()  # sem esperar de verdade os retries
    bot_mod.notify.push = lambda *a, **kw: None  # sem rede real
    try:
        store = FakeStore()
        fake_bot = FakeBot(fail_send_message=True)  # até o aviso de erro falha
        app = SimpleNamespace(bot_data={"cfg": _cfg(),
                                        "deps": {"jelly": None, "trakt": None, "mdb": None,
                                                "store": store}},
                              bot=fake_bot)
        asyncio.run(bot_mod._post_init(app))  # não pode propagar
    finally:
        bot_mod.build_digest, bot_mod._sleep = orig_build_digest, orig_sleep
        bot_mod.notify.push = orig_push
