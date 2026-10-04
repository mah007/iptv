"""The Arabic/English normaliser shared by the matcher and search (SPEC §7.2, §7.8)."""

import pytest

from apps.search.normalize import normalize, tokens


@pytest.fixture(autouse=True)
def _isolated_settings_cache() -> None:
    """Override the shared autouse fixture: nothing here reads the settings registry,
    so these pure tests also run on a host without the dev stack's redis-cache."""


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Case, punctuation, whitespace.
        ("The Matrix", "matrix"),
        ("THE  MATRIX!!", "matrix"),
        ("Mission: Impossible \u2013 Dead Reckoning", "mission impossible dead reckoning"),
        ("Spider-Man: No Way Home", "spider man no way home"),
        ("Schindler's List", "schindlers list"),
        ("Schindler\u2019s List", "schindlers list"),
        ("  Ocean's   Eleven ", "oceans eleven"),
        ("Fast & Furious", "fast furious"),
        ("Breaking.Bad", "breaking bad"),
        # Leading articles only (inner ones are kept).
        ("A Beautiful Mind", "beautiful mind"),
        ("An Education", "education"),
        ("Raiders of the Lost Ark", "raiders of the lost ark"),
        ("Al-Risalah", "risalah"),
        ("El Camino", "camino"),
        # A title that is only an article keeps it.
        ("The", "the"),
        ("A", "a"),
        # Latin accents and compatibility forms.
        ("Amélie", "amelie"),
        ("Pokémon", "pokemon"),
        ("\uff2d\uff21\uff34\uff32\uff29\uff38", "matrix"),  # full-width
        ("Straße", "strasse"),
        # Arabic: alef forms, alef maksura, teh marbuta, tashkeel, tatweel.
        ("أحمد", "احمد"),
        ("إسلام", "اسلام"),
        ("آمال", "امال"),
        ("مصطفى", "مصطفي"),
        ("الرسالة", "رساله"),
        ("مَدْرَسَة", "مدرسه"),
        ("مـــدرســة", "مدرسه"),
        ("الفيل الأزرق", "فيل ازرق"),
        ("الفيل الأزرق ٢", "فيل ازرق 2"),
        ("باب الحارة", "باب حاره"),
        ("صراع في الوادي", "صراع في وادي"),
        # The article is kept when too little would remain, and in the name of God.
        ("الو", "الو"),
        ("الله أكبر", "الله اكبر"),
        # Presentation forms and the lam-alef ligature (NFKC).
        ("ﻻ", "لا"),
        ("ﺍﻟﺮﺳﺎﻟﺔ", "رساله"),
        # Arabic punctuation and invisible marks.
        ("ماذا؟ لماذا،", "ماذا لماذا"),
        ("\u200f" + "الرسالة" + "\u200e", "رساله"),  # RLM and LRM marks
        # Persian digits and yeh.
        ("۱۹۷۶", "1976"),
        ("فارسی", "فارسي"),
        # Mixed scripts.
        ("The Message الرسالة (1976)", "message رساله 1976"),
        ("", ""),
        ("   ", ""),
        ("!!!", ""),
    ],
)
def test_normalize(text: str, expected: str) -> None:
    assert normalize(text) == expected


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("The Matrix", "Matrix"),
        ("Schindler's List", "Schindlers.List"),
        ("الرسالة", "رسالة"),
        ("أفلام", "افلام"),
        ("إسماعيلية رايح جاي", "اسماعيليه رايح جاي"),
        ("مدرسة المشاغبين", "مدرسه المشاغبين"),
        ("Amélie", "AMELIE"),
    ],
)
def test_spelling_variants_normalise_alike(left: str, right: str) -> None:
    assert normalize(left) == normalize(right)


def test_tokens_are_the_words_of_normalize() -> None:
    assert tokens("The Lord of the Rings") == ["lord", "of", "the", "rings"]
    assert tokens("") == []


def test_normalize_is_idempotent() -> None:
    for text in ("The Matrix", "الفيل الأزرق", "Amélie", "Al-Risalah", "مَدْرَسَة"):
        once = normalize(text)
        assert normalize(once) == once
