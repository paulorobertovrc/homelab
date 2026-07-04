from languages import to_code, same_language


def test_english_maps_to_en():
    assert to_code("English") == "en"


def test_russian_maps_to_ru():
    assert to_code("Russian") == "ru"


def test_portuguese_maps_to_pt():
    assert to_code("Portuguese") == "pt"


def test_unknown_language_returns_none():
    assert to_code("Klingon") is None


def test_same_language_true_case_insensitive():
    assert same_language("English", "en") is True


def test_same_language_false():
    assert same_language("English", "ru") is False


def test_same_language_unknown_name_is_false():
    assert same_language("Klingon", "en") is False
