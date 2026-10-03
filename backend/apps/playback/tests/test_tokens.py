"""The backend's media-token signer and verifier against the shared ADR-0007 vectors."""

import importlib.util
import json
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from django.conf import settings
from hypothesis import given
from hypothesis import strategies as st
from pytest_django import Settings

from apps.playback import tokens
from apps.playback.tokens import KeySetError, TokenError, Verdict

VECTORS_COPY = Path(__file__).parent / "data" / "token_vectors.json"
# The canonical file: mounted into the dev containers, or the repository checkout.
CANONICAL = (
    Path("/streaming/tests/vectors.json"),
    settings.BASE_DIR.parent / "streaming" / "tests" / "vectors.json",
)
REFERENCES = (
    Path("/streaming/tools/sign_token.py"),
    settings.BASE_DIR.parent / "streaming" / "tools" / "sign_token.py",
)
VECTORS: dict[str, Any] = json.loads(VECTORS_COPY.read_text(encoding="utf-8"))
KEYS = tokens.parse_keyset(VECTORS["keys"])
NOW: int = VECTORS["now"]


def _first_existing(paths: tuple[Path, ...]) -> Path | None:
    return next((path for path in paths if path.is_file()), None)


def test_vectors_copy_matches_the_canonical_file() -> None:
    canonical = _first_existing(CANONICAL)
    if canonical is None:
        pytest.skip("streaming/tests is not mounted here; the copy cannot be compared")
    assert VECTORS_COPY.read_bytes() == canonical.read_bytes(), (
        "apps/playback/tests/data/token_vectors.json is stale: copy streaming/tests/vectors.json"
    )


def test_policy_constants_match_the_vectors() -> None:
    assert VECTORS["clock_skew_s"] == tokens.CLOCK_SKEW_S
    assert VECTORS["max_ttl_s"] == tokens.MAX_TTL_S


@pytest.mark.parametrize("case", VECTORS["cases"], ids=lambda case: case["name"])
def test_vector_verdicts(case: dict[str, Any]) -> None:
    now = case.get("now", NOW)
    if case["verdict"] == "ok":
        claims = tokens.verify(
            KEYS, case["token"], now=now, client_ip=case["client_ip"], tail=case["tail"]
        )
        assert claims == tokens.Claims(**case["claims"])
    else:
        with pytest.raises(TokenError) as excinfo:
            tokens.verify(
                KEYS, case["token"], now=now, client_ip=case["client_ip"], tail=case["tail"]
            )
        assert excinfo.value.reason == case["verdict"]


@pytest.mark.parametrize(
    "case",
    [case for case in VECTORS["cases"] if case["verdict"] == "ok"],
    ids=lambda case: case["name"],
)
def test_valid_vectors_resign_identically(case: dict[str, Any]) -> None:
    claims = case["claims"]
    assert (
        tokens.sign(
            KEYS,
            session=claims["session"],
            title=claims["title"],
            rendition=claims["rendition"],
            exp=claims["exp"],
            net=claims["net"],
            kid=claims["kid"],
        )
        == case["token"]
    )


@pytest.mark.parametrize("entry", VECTORS["client_nets"], ids=lambda entry: entry["address"])
def test_client_net_vectors(entry: dict[str, Any]) -> None:
    if entry["net"] is None:
        with pytest.raises(ValueError, match=r".+"):
            tokens.client_net(entry["address"])
    else:
        assert tokens.client_net(entry["address"]) == entry["net"]


def _token(**overrides: Any) -> str:
    fields: dict[str, Any] = {
        "session": "0" * 32,
        "title": "0192f3a4-b5c6-7d8e-9f00-1122334455aa",
        "rendition": "compat",
        "exp": NOW + 600,
    }
    return tokens.sign(KEYS, **{**fields, **overrides})


def test_tampered_tokens_fail() -> None:
    token = _token()
    flipped = token[:-1] + ("A" if token[-1] != "A" else "B")
    with pytest.raises(TokenError) as excinfo:
        tokens.verify(KEYS, flipped, now=NOW, tail="compat.mp4")
    assert excinfo.value.reason is Verdict.BAD_SIGNATURE
    other_title = token.replace("0192f3a4-b5c6", "0192f3a4-b5c7", 1)
    with pytest.raises(TokenError) as excinfo:
        tokens.verify(KEYS, other_title, now=NOW, tail="compat.mp4")
    assert excinfo.value.reason is Verdict.BAD_SIGNATURE


def test_expired_tokens_fail_after_the_skew() -> None:
    token = _token(exp=NOW)
    assert tokens.verify(KEYS, token, now=NOW + tokens.CLOCK_SKEW_S - 1).exp == NOW
    with pytest.raises(TokenError) as excinfo:
        tokens.verify(KEYS, token, now=NOW + tokens.CLOCK_SKEW_S)
    assert excinfo.value.reason is Verdict.EXPIRED


def test_unknown_kid_fails() -> None:
    stranger = tokens.KeySet(tokens.Key("k9", bytes(range(1, 33))))
    token = tokens.sign(stranger, session="0" * 32, title="t", rendition="compat", exp=NOW + 60)
    with pytest.raises(TokenError) as excinfo:
        tokens.verify(KEYS, token, now=NOW)
    assert excinfo.value.reason is Verdict.UNKNOWN_KID


def test_previous_key_still_verifies() -> None:
    token = _token(kid="k0")
    assert tokens.verify(KEYS, token, now=NOW, tail="compat.mp4").kid == "k0"


@pytest.mark.parametrize(
    ("field", "value"),
    [("session", "ABC"), ("title", "a/b"), ("rendition", "compat.mp4"), ("net", "zz")],
)
def test_sign_refuses_fields_outside_their_grammar(field: str, value: str) -> None:
    with pytest.raises(ValueError, match=field):
        _token(**{field: value})


def test_sign_refuses_non_integer_expiry() -> None:
    with pytest.raises(ValueError, match="exp"):
        _token(exp=True)


# --- Key files ----------------------------------------------------------------------


def _write_keys(path: Path, obj: object) -> Path:
    path.write_text(json.dumps(obj), encoding="utf-8")
    return path


@pytest.fixture
def key_file(tmp_path: Path, settings: Settings) -> Iterator[Path]:
    path = _write_keys(tmp_path / "keys.json", VECTORS["keys"])
    settings.MEDIA_TOKEN_KEYS_FILE = str(path)
    tokens.reset_keyring()
    yield path
    tokens.reset_keyring()


def test_keyring_loads_the_configured_file(key_file: Path) -> None:
    assert tokens.keyring() == KEYS
    assert tokens.keyring() is tokens.keyring()


def test_keyring_follows_a_rotated_file(key_file: Path) -> None:
    before = tokens.keyring()
    rotated = {"current": {"kid": "k2", "secret": tokens.b64url(bytes(range(64, 96)))}}
    rotated["previous"] = VECTORS["keys"]["current"]
    _write_keys(key_file, rotated)
    after = tokens.keyring()
    assert after.current.kid == "k2"
    assert after.previous == before.current


def test_missing_key_file_is_reported(settings: Settings, tmp_path: Path) -> None:
    settings.MEDIA_TOKEN_KEYS_FILE = str(tmp_path / "absent.json")
    tokens.reset_keyring()
    with pytest.raises(KeySetError, match="cannot read key file"):
        tokens.keyring()


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ('{"current": {"kid": "k1", "secret": "AAEC', "not valid JSON"),
        ('{"current": {"kid": "k1", "secret": "short"}}', "current.secret"),
        ('{"current": {"kid": "k1", "secret": "%s"}, "extra": 1}', "unknown fields"),
        ("[]", "JSON object"),
    ],
)
def test_bad_key_files_are_refused_without_quoting_secrets(
    tmp_path: Path, content: str, message: str
) -> None:
    secret = VECTORS["keys"]["current"]["secret"]
    path = tmp_path / "keys.json"
    path.write_text(content.replace("%s", secret), encoding="utf-8")
    with pytest.raises(KeySetError, match=message) as excinfo:
        tokens.load_keyset(path)
    assert secret not in str(excinfo.value)


def test_duplicate_kids_are_refused() -> None:
    with pytest.raises(KeySetError, match="different kids"):
        tokens.parse_keyset(
            {
                "current": VECTORS["keys"]["current"],
                "previous": {"kid": "k1", "secret": VECTORS["keys"]["previous"]["secret"]},
            }
        )


# --- Differential check against the reference implementation -------------------------


def _reference() -> ModuleType | None:
    path = _first_existing(REFERENCES)
    if path is None:
        return None
    spec = importlib.util.spec_from_file_location("sign_token_reference", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclasses look the module up while the class bodies run.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


REFERENCE = _reference()
_SAFE = st.text(alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-.")


@pytest.mark.skipif(REFERENCE is None, reason="streaming/tools is not mounted here")
@given(
    session=st.text(alphabet="0123456789abcdef", min_size=32, max_size=32),
    title=st.from_regex(tokens.TITLE, fullmatch=True),
    rendition=st.from_regex(tokens.RENDITION, fullmatch=True),
    exp=st.integers(min_value=NOW - 100, max_value=NOW + tokens.MAX_TTL_S + 100),
    net=st.none() | st.from_regex(tokens.NET, fullmatch=True),
    tail=_SAFE,
    mutation=st.integers(min_value=0, max_value=300),
)
def test_signer_and_verifier_agree_with_the_reference(
    *, session: str, title: str, rendition: str, exp: int, net: str | None, tail: str, mutation: int
) -> None:
    assert REFERENCE is not None
    reference_keys = REFERENCE.parse_keyset(VECTORS["keys"])
    fields: dict[str, Any] = {
        "session": session,
        "title": title,
        "rendition": rendition,
        "exp": exp,
        "net": net,
    }
    token = tokens.sign(KEYS, **fields)
    assert token == REFERENCE.sign(reference_keys, **fields)
    # Flip one character (or none) and compare the two verdicts.
    if mutation < len(token):
        token = token[:mutation] + ("x" if token[mutation] != "x" else "y") + token[mutation + 1 :]
    client = "203.0.113.7"

    def outcome(module: Any, keys: Any) -> str:
        try:
            module.verify(keys, token, now=NOW, client_ip=client, tail=tail or None)
        except module.TokenError as exc:
            return str(exc.reason)
        return "ok"

    assert outcome(tokens, KEYS) == outcome(REFERENCE, reference_keys)
