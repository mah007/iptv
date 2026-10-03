"""Text normaliser shared by the TMDB matcher and search (SPEC §7.2 step 4, §7.8).

Both sides of every comparison (a parsed file name and a provider title, a search
query and the indexed titles) go through `normalize`, so the rules only have to
be consistent, not linguistically perfect. In order:

1. Unicode NFKC: full-width forms, ligatures and Arabic presentation forms
   (for example the lam-alef ligature) become their plain letters.
2. Case folding (a stronger, Unicode-aware lowercase).
3. Arabic folding: the hamza and madda alef forms (أ إ آ ٱ) to a bare alef,
   alef maksura and Farsi yeh to Arabic yeh, teh marbuta to heh, and
   Arabic-Indic and Eastern Arabic-Indic digits to ASCII digits.
4. Combining marks (tashkeel, Quranic marks, Latin accents) and invisible
   format characters (tatweel, ZWJ/ZWNJ, bidi marks) are removed.
5. Apostrophes are deleted ("Schindler's" -> "schindlers"); every other
   punctuation or symbol character becomes a space.
6. Articles: leading English ("the", "a", "an") and transliterated Arabic
   ("al", "el") articles are dropped from the start of the text, and the Arabic
   article "ال" from the start of every Arabic word that keeps at least two
   letters (the clitic attaches to each noun: "الفيل الأزرق" -> "فيل ازرق").
   Stripping repeats, because alef folding can expose a new "ال": "الألعاب",
   "ألعاب" and "العاب" all reduce to "عاب". The name of God is left intact.
7. Whitespace is collapsed and trimmed.

A text that would normalise to nothing because it *is* an article ("The",
"A") keeps the article instead. `normalize` is idempotent.
"""

import re
import unicodedata
from typing import Final

__all__ = ["normalize", "tokens"]

# Arabic letters folded onto one representative (step 3).
_ALEF: Final = "\u0627"
_YEH: Final = "\u064a"
_HEH: Final = "\u0647"
_ARABIC_FOLD: Final = str.maketrans(
    {
        "\u0623": _ALEF,  # alef with hamza above
        "\u0625": _ALEF,  # alef with hamza below
        "\u0622": _ALEF,  # alef with madda above
        "\u0671": _ALEF,  # alef wasla
        "\u0672": _ALEF,  # alef with wavy hamza above
        "\u0673": _ALEF,  # alef with wavy hamza below
        "\u0649": _YEH,  # alef maksura
        "\u06cc": _YEH,  # Farsi yeh
        "\u0629": _HEH,  # teh marbuta
        **{chr(0x0660 + digit): str(digit) for digit in range(10)},  # Arabic-Indic digits
        **{chr(0x06F0 + digit): str(digit) for digit in range(10)},  # Eastern Arabic-Indic
    }
)

_TATWEEL: Final = "\u0640"
# ' and its look-alikes: left/right single quotes, modifier letter apostrophe,
# backtick, acute accent, modifier letter prime, prime.
_APOSTROPHES: Final = frozenset("'\u2018\u2019\u02bc`\u00b4\u02b9\u2032")
_LEADING_ARTICLES: Final = frozenset({"the", "a", "an", "al", "el"})
_ARABIC_ARTICLE: Final = "ال"  # ال (already alef-folded)
_ALLAH: Final = "الله"  # الله after folding
_ARABIC_LETTER: Final = re.compile(r"[ؠ-يٮ-ۓەۺ-ۿ]")
_WHITESPACE: Final = re.compile(r"\s+")


def _char_class(char: str) -> str:
    """'' to drop the character, ' ' to turn it into a separator, or itself."""
    if char == _TATWEEL or char in _APOSTROPHES:
        return ""
    category = unicodedata.category(char)
    if category in {"Mn", "Me", "Cf"}:
        return ""
    if category[0] in {"P", "S"}:
        return " "
    if category[0] in {"Z", "C"}:
        return " "
    return char


def _strip_arabic_article(word: str) -> str:
    while (
        word.startswith(_ARABIC_ARTICLE)
        and word != _ALLAH
        and len(word) - len(_ARABIC_ARTICLE) >= 2
        and _ARABIC_LETTER.match(word, len(_ARABIC_ARTICLE))
    ):
        word = word[len(_ARABIC_ARTICLE) :]
    return word


def tokens(text: str) -> list[str]:
    """The normalised words of `text` (see the module docstring for the rules)."""
    folded = unicodedata.normalize("NFKC", text).casefold().translate(_ARABIC_FOLD)
    # NFKD splits accents and hamza seats off their base letters so step 4 can drop them.
    decomposed = unicodedata.normalize("NFKD", folded)
    cleaned = "".join(_char_class(char) for char in decomposed)
    words = _WHITESPACE.split(unicodedata.normalize("NFC", cleaned).strip())
    words = [word for word in words if word]
    if not words:
        return []
    while len(words) > 1 and words[0] in _LEADING_ARTICLES:
        words = words[1:]
    return [_strip_arabic_article(word) for word in words]


def normalize(text: str) -> str:
    """Normalise a title or query for matching and search: `"The Matrix!"` -> `"matrix"`."""
    return " ".join(tokens(text))
