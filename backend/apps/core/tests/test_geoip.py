"""DB-IP Lite lookups and the monthly update (ADR-0021), on generated databases."""

import gzip
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from apps.core import geoip
from apps.core.services import set_setting
from apps.core.tests import geoip_data

pytestmark = pytest.mark.django_db


@pytest.fixture
def installed(geoip_dir: Path) -> Path:
    geoip_data.install(geoip_dir)
    return geoip_dir


def test_lookup_answers_country_and_network(installed: Path) -> None:
    assert geoip.lookup("81.2.69.142") == geoip.Geo(
        country="GB", asn=20712, asn_org="Example Broadband GB"
    )
    assert geoip.lookup("77.31.4.5").country == "SA"
    assert geoip.lookup("2a00:1450:4001::1").asn == 15169
    assert geoip.lookup("::ffff:8.8.8.8").country == "US"  # IPv4-mapped


@pytest.mark.parametrize("address", ["10.1.2.3", "192.168.1.5", "127.0.0.1", "fe80::1"])
def test_local_addresses_are_local(installed: Path, address: str) -> None:
    assert geoip.lookup(address) == geoip.Geo(local=True)


@pytest.mark.parametrize("address", [None, "", "not-an-ip", "224.0.0.1", "203.0.113.300"])
def test_nonsense_and_unknown_never_raise(installed: Path, address: str | None) -> None:
    assert geoip.lookup(address).country is None


def test_missing_database_and_switch_off(geoip_dir: Path) -> None:
    assert geoip.lookup("81.2.69.142") == geoip.Geo()
    assert not geoip.available()
    geoip_data.install(geoip_dir)
    geoip.reset_readers()
    assert geoip.lookup("81.2.69.142").country == "GB"
    set_setting("geoip.enabled", value=False, actor=None)
    assert geoip.lookup("81.2.69.142") == geoip.Geo()


def test_a_replaced_file_is_reopened(installed: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert geoip.lookup("41.32.0.9").country == "EG"
    monkeypatch.setattr(geoip, "RELOAD_CHECK_S", 0.0)
    # The monthly update replaces the file: the next lookup uses the new one.
    monkeypatch.setitem(geoip_data.NETWORKS, "41.32.0.0/16", ("LY", 1, "Moved"))
    tmp = installed / "new"
    tmp.mkdir()
    geoip_data.install(tmp)
    (tmp / "dbip-country-lite.mmdb").replace(installed / "dbip-country-lite.mmdb")
    assert geoip.lookup("41.32.0.9").country == "LY"


def test_status_reports_both_databases(installed: Path) -> None:
    status = geoip.status()
    assert status["enabled"] is True
    assert status["attribution"] == "https://db-ip.com"
    for kind in ("country", "asn"):
        assert status["databases"][kind]["installed"] is True
        assert status["databases"][kind]["size"] > 0
        assert status["databases"][kind]["built_at"]


# --- The update ---------------------------------------------------------------------------


def _transport(files: dict[str, bytes], seen: list[str]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        body = files.get(str(request.url))
        return httpx.Response(200, content=body) if body is not None else httpx.Response(404)

    return httpx.MockTransport(handler)


def _releases(tmp_path: Path, month: str) -> dict[str, bytes]:
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    return {
        geoip.url_for("country", month): geoip_data.gzipped(
            geoip_data.country_db(), work, "c.mmdb"
        ),
        geoip.url_for("asn", month): geoip_data.gzipped(geoip_data.asn_db(), work, "a.mmdb"),
    }


def test_update_installs_this_months_release(geoip_dir: Path, tmp_path: Path) -> None:
    now = datetime.now(tz=UTC)
    seen: list[str] = []
    files = _releases(tmp_path, now.strftime("%Y-%m"))
    result = geoip.update(transport=_transport(files, seen), now=now)
    assert result.installed == ("country", "asn")
    assert geoip.lookup("81.2.69.142").country == "GB"
    meta = geoip.status()["databases"]["country"]
    assert meta["updated_at"]
    # Nothing newer: the next run downloads nothing.
    seen.clear()
    again = geoip.update(transport=_transport(files, seen), now=now)
    assert again.current == ("country", "asn")
    assert seen == []


def test_early_in_a_month_last_months_release_is_used(geoip_dir: Path, tmp_path: Path) -> None:
    now = datetime.now(tz=UTC)
    previous = (now.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    seen: list[str] = []
    result = geoip.update(transport=_transport(_releases(tmp_path, previous), seen), now=now)
    assert result.installed == ("country", "asn")
    assert len(seen) == 4  # this month's 404, then last month's, for both


@pytest.mark.parametrize(
    ("corrupt", "reason"),
    [
        (b"not gzip at all", "gzip"),
        (gzip.compress(b"plain text, not a database"), "MaxMind"),
    ],
)
def test_bad_downloads_never_replace_the_database(
    installed: Path, tmp_path: Path, corrupt: bytes, reason: str
) -> None:
    now = datetime.now(tz=UTC)
    files = {geoip.url_for("country", now.strftime("%Y-%m")): corrupt}
    result = geoip.update(transport=_transport(files, []), force=True, now=now)
    assert "country" in result.failed
    assert any(reason in error for error in result.errors)
    assert geoip.lookup("81.2.69.142").country == "GB"  # the old one stays


def test_a_database_of_the_wrong_kind_is_refused(installed: Path, tmp_path: Path) -> None:
    now = datetime.now(tz=UTC)
    work = tmp_path / "w"
    work.mkdir()
    files = {
        geoip.url_for("country", now.strftime("%Y-%m")): geoip_data.gzipped(
            geoip_data.asn_db(), work, "x.mmdb"
        )
    }
    result = geoip.update(transport=_transport(files, []), force=True, now=now)
    assert "country" in result.failed


def test_a_redirect_away_from_db_ip_is_refused(installed: Path) -> None:
    now = datetime.now(tz=UTC)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == geoip.DOWNLOAD_HOST:
            return httpx.Response(302, headers={"Location": "https://evil.example/db.gz"})
        return httpx.Response(200, content=b"whatever")

    result = geoip.update(transport=httpx.MockTransport(handler), force=True, now=now)
    assert result.failed == ("country", "asn")


def test_an_old_build_is_refused(installed: Path, tmp_path: Path) -> None:
    later = datetime.now(tz=UTC) + timedelta(days=400)  # the file was built "long ago"
    files = _releases(tmp_path, later.strftime("%Y-%m"))
    result = geoip.update(transport=_transport(files, []), force=True, now=later)
    assert result.failed == ("country", "asn")
