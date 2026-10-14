"""Text that reaches admins and IPTV apps from outside (G-08; ADR-0021).

File names, provider metadata and guide data are untrusted. Unicode's bidirectional
override, embedding and isolate controls (U+202A to U+202E, U+2066 to U+2069) can make
text read differently from what it is in the Arabic and English UIs ("Trojan Source"
tricks such as `evil\\u202egpj.mkv`), so they are removed wherever such text is stored or
shown. The ordinary direction marks (LRM, RLM, ALM) stay: Arabic titles use them.
"""

from typing import Any, Final

from rest_framework import serializers

#: U+202A-U+202E (embeddings and overrides) and U+2066-U+2069 (isolates).
BIDI_CONTROLS: Final = frozenset(
    chr(code) for code in (*range(0x202A, 0x202F), *range(0x2066, 0x206A))
)
_TABLE: Final = dict.fromkeys(map(ord, BIDI_CONTROLS))


def strip_bidi(value: str) -> str:
    """`value` without bidirectional override, embedding and isolate controls."""
    return value.translate(_TABLE) if value else value


class SafeTextField(serializers.CharField):
    """A read-only text field shown without bidirectional controls (file names)."""

    def to_representation(self, value: Any) -> str:
        return strip_bidi(super().to_representation(value))
