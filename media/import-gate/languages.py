"""Map Servarr language names to ISO-639-1 codes (what faster-whisper returns)."""

_NAME_TO_CODE = {
    "english": "en",
    "portuguese": "pt",
    "russian": "ru",
    "spanish": "es",
    "french": "fr",
    "german": "de",
    "italian": "it",
    "japanese": "ja",
    "korean": "ko",
    "chinese": "zh",
    "hindi": "hi",
    "arabic": "ar",
    "dutch": "nl",
    "polish": "pl",
    "turkish": "tr",
    "swedish": "sv",
    "danish": "da",
    "norwegian": "no",
    "finnish": "fi",
    "ukrainian": "uk",
}


def to_code(name: str) -> str | None:
    if not name:
        return None
    return _NAME_TO_CODE.get(name.strip().lower())


def same_language(name: str, code: str) -> bool:
    mapped = to_code(name)
    return mapped is not None and mapped == code
