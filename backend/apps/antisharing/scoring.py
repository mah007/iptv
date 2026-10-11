"""The anti-sharing risk score (SPEC §11.1; G-27, ADR-0021). Pure functions, no database.

Input: what was seen of one customer in the last 24 hours (`Seen`: a device, a network
(/24 or /64), its ASN and country, and when). Output: an `Assessment` with every signal
against its limit, the points it adds, and the evidence behind it.

The signals, over rolling windows:

- **Networks (ASNs) in 1 h and in 24 h.** A phone on mobile data and a TV at home are two
  ASNs, one household: the default limits are 2 and 3.
- **Countries in 24 h** (default limit 1).
- **Impossible travel:** two sightings in countries whose centroids are at least
  `travel_min_km` apart, overlapping in time or closer in time than travelling at
  `travel_speed_kmh` allows.
- **Networks per IPTV login in 1 h:** networks of the same ASN count once unless they
  were used at the same time, so an ISP changing the address within one provider or city
  is ignored (§11.1).

Unknown ASNs count per network; local network sightings (no ASN, no country) are left
out. Flags never lock an account: the score only decides what the admin sees.
"""

import csv
import math
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import cache
from pathlib import Path
from typing import Final

#: Points per unit over a limit, per signal; documented in ADR-0021.
WEIGHTS: Final = {
    "asns_1h": 2,
    "asns_24h": 1,
    "countries_24h": 2,
    "impossible_travel": 3,
    "networks_per_credential_1h": 2,
}
MAX_TRAVEL_EVENTS: Final = 3
EARTH_RADIUS_KM: Final = 6371.0
_DATA: Final = Path(__file__).with_name("data") / "country_centroids.csv"


@dataclass(frozen=True, slots=True)
class Seen:
    device: str | None
    network: str
    asn: int | None
    country: str | None
    first: datetime
    last: datetime
    asn_org: str = ""


@dataclass(frozen=True, slots=True)
class Limits:
    max_asns_1h: int = 2
    max_asns_24h: int = 3
    max_countries_24h: int = 1
    max_networks_per_credential_1h: int = 2
    travel_speed_kmh: int = 900
    travel_min_km: int = 500
    flag_score: int = 4


@dataclass(frozen=True, slots=True)
class Signal:
    name: str
    value: int
    limit: int
    points: int


@dataclass(frozen=True, slots=True)
class Travel:
    from_country: str
    to_country: str
    km: int
    hours: float
    at: datetime


@dataclass(frozen=True, slots=True)
class Assessment:
    score: int
    flagged: bool
    signals: tuple[Signal, ...]
    travel: tuple[Travel, ...] = field(default=())

    def as_dict(self) -> dict[str, object]:
        return {
            "score": self.score,
            "flagged": self.flagged,
            "signals": [
                {"name": s.name, "value": s.value, "limit": s.limit, "points": s.points}
                for s in self.signals
            ],
            "travel": [
                {
                    "from": t.from_country,
                    "to": t.to_country,
                    "km": t.km,
                    "hours": round(t.hours, 2),
                    "at": t.at.isoformat(),
                }
                for t in self.travel
            ],
        }


@cache
def centroids() -> dict[str, tuple[float, float]]:
    """ISO country code -> (latitude, longitude) of its Natural Earth label point."""
    table: dict[str, tuple[float, float]] = {}
    with _DATA.open(encoding="utf-8") as handle:
        rows = (line for line in handle if not line.startswith("#"))
        for row in csv.DictReader(rows):
            table[row["code"]] = (float(row["lat"]), float(row["lon"]))
    return table


def distance_km(a: str, b: str) -> float | None:
    """Great-circle distance between two countries' centroids; None if one is unknown."""
    points = centroids()
    if a not in points or b not in points:
        return None
    (lat1, lon1), (lat2, lon2) = points[a], points[b]
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi, dlmb = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    h = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(h)))


def _is_local(seen: Seen) -> bool:
    return seen.asn is None and not seen.country and seen.network.startswith("local")


def _asn_key(seen: Seen) -> str:
    return f"as{seen.asn}" if seen.asn is not None else f"net:{seen.network}"


def _overlaps(a: Seen, b: Seen) -> bool:
    return a.first <= b.last and b.first <= a.last


def _gap_hours(a: Seen, b: Seen) -> float:
    """Hours between two sightings; 0 when they overlap."""
    if _overlaps(a, b):
        return 0.0
    gap = (b.first - a.last) if a.last < b.first else (a.first - b.last)
    return gap.total_seconds() / 3600


def _signal(name: str, value: int, limit: int) -> Signal:
    over = max(0, value - limit)
    return Signal(name, value, limit, over * WEIGHTS[name])


def impossible_travel(sightings: Sequence[Seen], limits: Limits) -> list[Travel]:
    """Pairs of sightings in distant countries that one household can't produce."""
    located = sorted((s for s in sightings if s.country), key=lambda s: s.first)
    events: list[Travel] = []
    seen_pairs: set[tuple[str, str]] = set()
    for i, later in enumerate(located):
        for earlier in located[:i]:
            first, second = earlier.country, later.country
            if not first or not second or first == second:
                continue
            pair = (min(first, second), max(first, second))
            if pair in seen_pairs:
                continue
            km = distance_km(first, second)
            if km is None or km < limits.travel_min_km:
                continue
            hours = _gap_hours(earlier, later)
            if hours > 0 and km / hours <= limits.travel_speed_kmh:
                continue
            seen_pairs.add(pair)
            events.append(Travel(first, second, round(km), hours, later.first))
            if len(events) >= MAX_TRAVEL_EVENTS:
                return events
    return events


def networks_per_credential(sightings: Iterable[Seen]) -> int:
    """The most networks one IPTV login used, same-ASN changes counted once unless
    concurrent."""
    by_device: dict[str, list[Seen]] = defaultdict(list)
    for seen in sightings:
        if seen.device is not None:
            by_device[seen.device].append(seen)
    best = 0
    for items in by_device.values():
        by_asn: dict[str, list[Seen]] = defaultdict(list)
        for seen in items:
            by_asn[_asn_key(seen)].append(seen)
        count = 0
        for group in by_asn.values():
            networks: dict[str, list[Seen]] = defaultdict(list)
            for seen in group:
                networks[seen.network].append(seen)
            if len(networks) == 1:
                count += 1
                continue
            # Networks of one ASN used at the same time are separate places.
            names = list(networks)
            concurrent = {
                name
                for name in names
                for other in names
                if other != name
                and any(_overlaps(a, b) for a in networks[name] for b in networks[other])
            }
            count += max(1, len(concurrent))
        best = max(best, count)
    return best


def assess(sightings: Iterable[Seen], now: datetime, limits: Limits) -> Assessment:
    window_24h = [s for s in sightings if s.last >= now - timedelta(hours=24)]
    window_24h = [s for s in window_24h if not _is_local(s)]
    window_1h = [s for s in window_24h if s.last >= now - timedelta(hours=1)]
    travel = impossible_travel(window_24h, limits)
    signals = (
        _signal("asns_1h", len({_asn_key(s) for s in window_1h}), limits.max_asns_1h),
        _signal("asns_24h", len({_asn_key(s) for s in window_24h}), limits.max_asns_24h),
        _signal(
            "countries_24h",
            len({s.country for s in window_24h if s.country}),
            limits.max_countries_24h,
        ),
        Signal(
            "impossible_travel",
            len(travel),
            0,
            len(travel) * WEIGHTS["impossible_travel"],
        ),
        _signal(
            "networks_per_credential_1h",
            networks_per_credential(window_1h),
            limits.max_networks_per_credential_1h,
        ),
    )
    score = sum(signal.points for signal in signals)
    return Assessment(score, score >= limits.flag_score, signals, tuple(travel))
