"""SPEC §7.4 check 4: IP and country rules, the customer's own and the global ones.

The customer's rules travel in their entitlement (`ip_rules`, `country_rules`);
global rules (`AccessRule.user` null) are read here, so changing one never
rewrites every entitlement (ADR-0006). Semantics, applied to both sets together:

1. `ip_allow` matching the client exempts it from every other rule (a trusted
   address, e.g. an admin's test line abroad).
2. `ip_deny` or `cidr_deny` matching the client: refused (IP_BLOCKED).
3. `country_deny` matching the client's country: refused (GEO_BLOCKED).
4. Any `country_allow` rule makes the allowed countries a closed list: a client
   outside it, or whose country is unknown, is refused (GEO_BLOCKED).

Expired rules never apply. Countries come from the caller (a geo-IP lookup); the
POC has none yet, so only allow-lists are affected by an unknown country.
"""

import ipaddress
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from django.db.models import Q

from apps.accounts.models import AccessRule, AccessRuleType
from apps.playback.entitlements import EntitlementRule

type IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


class RuleVerdict(StrEnum):
    ALLOWED = "allowed"
    IP_BLOCKED = "ip_blocked"
    GEO_BLOCKED = "geo_blocked"


@dataclass(frozen=True, slots=True)
class Rule:
    type: str
    value: str


def _active(rule: EntitlementRule, now: datetime) -> bool:
    expires_at = rule["expires_at"]
    return expires_at is None or datetime.fromisoformat(expires_at) > now


def customer_rules(rules: Iterable[EntitlementRule], now: datetime) -> list[Rule]:
    return [Rule(rule["type"], rule["value"]) for rule in rules if _active(rule, now)]


def global_rules(now: datetime) -> list[Rule]:
    """The unexpired rules that apply to everyone (one query)."""
    rows = (
        AccessRule.objects.filter(user__isnull=True)
        .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
        .values_list("type", "value")
    )
    return [Rule(rule_type, value) for rule_type, value in rows]


def _address(client_ip: str | None) -> IPAddress | None:
    if not client_ip:
        return None
    try:
        address = ipaddress.ip_address(client_ip)
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def _matches_ip(rule: Rule, address: IPAddress) -> bool:
    try:
        if rule.type == AccessRuleType.CIDR_DENY:
            return address in ipaddress.ip_network(rule.value, strict=False)
        return _address(rule.value) == address
    except ValueError:
        return False


def evaluate(rules: Iterable[Rule], *, client_ip: str | None, country: str | None) -> RuleVerdict:
    rules = list(rules)
    address = _address(client_ip)
    if address is not None:
        by_type = {
            kind: [rule for rule in rules if rule.type == kind]
            for kind in (AccessRuleType.IP_ALLOW, AccessRuleType.IP_DENY, AccessRuleType.CIDR_DENY)
        }
        if any(_matches_ip(rule, address) for rule in by_type[AccessRuleType.IP_ALLOW]):
            return RuleVerdict.ALLOWED
        denies = [*by_type[AccessRuleType.IP_DENY], *by_type[AccessRuleType.CIDR_DENY]]
        if any(_matches_ip(rule, address) for rule in denies):
            return RuleVerdict.IP_BLOCKED
    code = (country or "").upper() or None
    denied = {rule.value for rule in rules if rule.type == AccessRuleType.COUNTRY_DENY}
    allowed = {rule.value for rule in rules if rule.type == AccessRuleType.COUNTRY_ALLOW}
    if code is not None and code in denied:
        return RuleVerdict.GEO_BLOCKED
    if allowed and code not in allowed:
        return RuleVerdict.GEO_BLOCKED
    return RuleVerdict.ALLOWED
