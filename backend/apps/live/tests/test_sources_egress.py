"""Source URLs (validation, display, redaction) and where admin URLs may point (G-11)."""

import httpx
import pytest
from django.core.exceptions import ValidationError

from apps.live import egress, sources
from apps.live.egress import UnsafeDestination, check_host, check_url, check_url_static

SECRET_URL = "rtmp://user:pa55word@encoder.example.net:1935/live/stream-key-91ab?token=abc12345"  # noqa: S105 (a fake)


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/live.m3u8",
        "https://example.com:8443/a.ts",
        "rtmp://example.com/live/key",
        "rtmps://example.com/live/key",
        "rtsp://192.168.1.20:554/stream",
        "rtsps://cam.local/stream",
        "srt://example.com:9000",
    ],
)
def test_allowed_schemes(url: str) -> None:
    assert sources.validate_source_url(f"  {url} ") == url


@pytest.mark.parametrize(
    "url",
    [
        "",
        "file:///etc/passwd",
        "concat:a|b",
        "udp://239.0.0.1:1234",
        "http://",
        "http://exa mple.com/",
        "http://example.com/\nX-Injected: 1",
        "http://example.com:99999/",
        "https://" + "a" * 3000 + ".com/",
    ],
)
def test_refused_urls(url: str) -> None:
    with pytest.raises(ValidationError):
        sources.validate_source_url(url)


def test_http_only_urls() -> None:
    assert sources.validate_http_url("https://guide.example.com/x.xml")
    with pytest.raises(ValidationError):
        sources.validate_http_url("rtsp://guide.example.com/x")


def test_describe_shows_scheme_host_and_port_only() -> None:
    info = sources.describe(SECRET_URL)
    assert info.as_dict() == {"scheme": "rtmp", "host": "encoder.example.net", "port": 1935}
    encrypted = sources.encrypt(SECRET_URL)
    assert "pa55word" not in encrypted
    assert sources.describe_encrypted(encrypted) == info
    assert sources.describe_encrypted("") is None
    assert sources.describe_encrypted("not-a-token") is None


def test_the_redactor_masks_every_form_of_the_url() -> None:
    redact = sources.Redactor(SECRET_URL)
    text = (
        f"[tcp] Failed to open {SECRET_URL}: refused; key /live/stream-key-91ab "
        "password pa55word, user user, query token=abc12345"
    )
    masked = redact(text)
    for secret in ("pa55word", "stream-key-91ab", "abc12345"):
        assert secret not in masked
    assert sources.SOURCE_MASK in masked


def test_input_args_per_scheme() -> None:
    assert "-rtsp_transport" in sources.input_args("rtsp://a.example/s")
    http = sources.input_args("https://a.example/s.m3u8")
    assert "-reconnect" in http
    assert http[:2] == ["-protocol_whitelist", sources.PROTOCOL_WHITELIST]
    assert "file" not in sources.PROTOCOL_WHITELIST.split(",")


# --- Where URLs may point (threat model G-11) -----------------------------------------------


def resolver(table: dict[str, list[str]]) -> egress.Resolver:
    def resolve(host: str, port: int | None) -> list[str]:
        if host not in table:
            raise OSError("unknown host")
        return table[host]

    return resolve


@pytest.fixture(autouse=True)
def _fresh_internal_addresses() -> None:
    egress.INTERNAL.until = 0.0


DNS = resolver(
    {
        "postgres": ["172.20.0.5"],
        "web": ["172.20.0.6"],
        "encoder.lan": ["192.168.1.20"],
        "ersatztv": ["172.21.0.9"],
        "rebind.example": ["127.0.0.1"],
        "meta.example": ["169.254.169.254"],
        "both.example": ["203.0.113.5", "172.20.0.5"],
        "public.example": ["203.0.113.5", "2001:db8::5"],
        "v6meta.example": ["fd00:ec2::254"],
    }
)


@pytest.mark.parametrize(
    "host", ["encoder.lan", "ersatztv", "public.example", "192.168.1.20", "10.0.0.7"]
)
def test_lan_and_public_hosts_are_allowed(host: str) -> None:
    check_host(host, None, resolver=DNS)


@pytest.mark.parametrize(
    ("host", "reason"),
    [
        ("postgres", "internal_host"),
        ("Redis-State", "internal_host"),
        ("web.", "internal_host"),
        ("live-relay", "internal_host"),
        ("localhost", "internal_host"),
        ("tv.localhost", "internal_host"),
        ("127.0.0.1", "loopback"),
        ("::1", "loopback"),
        ("[::ffff:127.0.0.1]", "loopback"),
        ("169.254.169.254", "metadata_address"),
        ("fd00:ec2::254", "metadata_address"),
        ("169.254.10.10", "link_local"),
        ("fe80::1", "link_local"),
        ("0.0.0.0", "special_address"),  # noqa: S104 (a refused destination)
        ("224.0.0.1", "special_address"),
        ("rebind.example", "loopback"),
        ("meta.example", "metadata_address"),
        ("v6meta.example", "metadata_address"),
        ("both.example", "internal_host"),  # resolves to postgres's address too
        ("172.20.0.6", "internal_host"),  # web's address
        ("nowhere.example", "unresolvable"),
        ("", "no_host"),
    ],
)
def test_refused_destinations(host: str, reason: str) -> None:
    with pytest.raises(UnsafeDestination) as refused:
        check_host(host, None, resolver=DNS)
    assert refused.value.reason == reason


def test_an_integration_host_is_the_exception() -> None:
    table = resolver({"mediamtx": ["172.20.0.5"]})  # shares an address with postgres
    with pytest.raises(UnsafeDestination):
        check_host(
            "mediamtx",
            None,
            resolver=resolver({"mediamtx": ["172.20.0.5"], "postgres": ["172.20.0.5"]}),
        )
    check_host("mediamtx", None, allowed=["MediaMTX"], resolver=table)


def test_check_url_and_the_static_check() -> None:
    check_url("rtsp://encoder.lan:8554/cam", resolver=DNS)
    with pytest.raises(UnsafeDestination):
        check_url("http://web:8000/internal/stream-auth", resolver=DNS)
    check_url_static("rtsp://anything.example/cam")  # no DNS: names are checked later
    for url in ("http://web:8000/", "http://127.0.0.1/", "http://[fe80::1]/x"):
        with pytest.raises(UnsafeDestination):
            check_url_static(url)


def test_guarded_clients_check_every_redirect() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "public.example":
            return httpx.Response(302, headers={"Location": "http://169.254.169.254/latest/"})
        return httpx.Response(200, content=b"metadata")

    client = egress.guarded_client(
        resolver=DNS, transport=httpx.MockTransport(handler), follow_redirects=True
    )
    with pytest.raises(UnsafeDestination):
        client.get("http://public.example/guide.xml")
    with pytest.raises(UnsafeDestination):
        client.get("http://web:8000/")


def test_integration_hosts(db: None) -> None:
    from apps.live.models import LiveIntegration  # noqa: PLC0415

    LiveIntegration.objects.create(
        kind="mediamtx",
        name="Studio",
        base_url="http://mediamtx:9997",
        stream_base_url="rtsp://MediaMTX:8554",
        rights_holder="Us",
    )
    assert egress.integration_hosts() == {"mediamtx"}
