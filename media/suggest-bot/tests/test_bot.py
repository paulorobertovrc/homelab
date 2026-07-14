from bot import keyboard_for, status_suffix
from pipeline import Suggestion


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
