"""Small generated DB-IP Lite look-alike databases for tests (ADR-0021): no download.

The networks are real public ranges, so the code under test treats them as the
internet; the answers are made up for the tests and say nothing about those networks.
"""

import gzip
from pathlib import Path
from typing import Final

from mmdb_writer import MMDBWriter
from netaddr import IPSet  # type: ignore[import-untyped]  # dev only, no stubs

#: network -> (country, ASN, organisation)
NETWORKS: Final[dict[str, tuple[str, int, str]]] = {
    "81.2.69.0/24": ("GB", 20712, "Example Broadband GB"),
    "8.8.8.0/24": ("US", 15169, "Example US"),
    "77.31.0.0/16": ("SA", 25019, "Example Telecom SA"),
    "77.32.0.0/16": ("SA", 39891, "Example Mobile SA"),
    "41.32.0.0/16": ("EG", 8452, "Example Data EG"),
    "5.30.0.0/16": ("AE", 5384, "Example Telecom AE"),
    "2a00:1450::/32": ("US", 15169, "Example US"),
}


def country_db() -> MMDBWriter:
    writer = MMDBWriter(ip_version=6, database_type="DBIP-Country-Lite", ipv4_compatible=True)
    for network, (country, _asn, _org) in NETWORKS.items():
        writer.insert_network(IPSet([network]), {"country": {"iso_code": country}})
    return writer


def asn_db() -> MMDBWriter:
    writer = MMDBWriter(
        ip_version=6, database_type="DBIP-ASN-Lite (compat=GeoLite2-ASN)", ipv4_compatible=True
    )
    for network, (_country, asn, org) in NETWORKS.items():
        writer.insert_network(
            IPSet([network]),
            {"autonomous_system_number": asn, "autonomous_system_organization": org},
        )
    return writer


def install(folder: Path) -> None:
    """Write both databases where `apps.core.geoip` reads them."""
    country_db().to_db_file(str(folder / "dbip-country-lite.mmdb"))
    asn_db().to_db_file(str(folder / "dbip-asn-lite.mmdb"))


def gzipped(writer: MMDBWriter, work: Path, name: str) -> bytes:
    """A database as DB-IP serves it (gzip)."""
    path = work / name
    writer.to_db_file(str(path))
    return gzip.compress(path.read_bytes())
