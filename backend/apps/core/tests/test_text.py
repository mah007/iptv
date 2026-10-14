"""Bidirectional controls never reach admins or IPTV apps (G-08, ADR-0021)."""

from apps.core.text import BIDI_CONTROLS, SafeTextField, strip_bidi
from apps.metadata import services as metadata
from apps.xtream_api.dto import Text

TROJAN = "evil" + chr(0x202E) + "vkm.exe" + chr(0x2066) + "x" + chr(0x2069)


def test_controls_are_removed_and_marks_stay() -> None:
    assert len(BIDI_CONTROLS) == 9
    assert strip_bidi(TROJAN) == "evilvkm.exex"
    rlm = chr(0x200F)
    assert strip_bidi(f"الماتريكس{rlm} 1999") == f"الماتريكس{rlm} 1999"
    assert strip_bidi("") == ""


def test_provider_text_xtream_text_and_file_names() -> None:
    assert metadata._str(f"  {TROJAN}  ") == "evilvkm.exex"
    assert Text(en=TROJAN, ar="").pick("en") == "evilvkm.exex"
    assert SafeTextField().to_representation(TROJAN) == "evilvkm.exex"
