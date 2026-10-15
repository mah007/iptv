"""Who can't sign in right now, and unlocking them (SPEC §8.3 Security → Lockouts; ADR-0023).

Five kinds of lockout, each read from where it already lives (nothing new is recorded on
any sign-in path):

- `sign_in`: django-axes' failures per login and address (admin, portal and app sign-ins),
  locked from `AXES_FAILURE_LIMIT` for the doubling cool-off of `apps.accounts.lockout`;
- `second_factor`: an admin's wrong authenticator codes or keys (`mfa:fail:<user>`);
- `confirmation`: wrong "Confirm it's you" answers, admins' codes or customers' passwords
  (`stepup:fail:<user>`);
- `xtream_login`: a TV login paused after failed sign-ins from one network, or from all
  networks together (`xc:fail:user*`, which hold only a digest of the login: the page
  names the login when it is one of ours);
- `xtream_address`: an address paused after failed TV sign-ins (`xc:block:ip:*`).

Each lockout has an opaque `id` the admin passes back to unlock it. Unlocking is audited
(`lockout.unlock`); the view asks for a recent confirmation (it lifts a protection) and,
for an admin's account, `admins.manage`.
"""

import ipaddress
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final, cast
from uuid import UUID

import redis
from axes.models import AccessAttempt
from axes.utils import reset as axes_reset
from django.conf import settings
from django.db.models import Q
from django.db.models.functions import Lower

from apps.accounts import auth, stepup
from apps.accounts.lockout import cool_off_for
from apps.accounts.models import User, XtreamCredential
from apps.audit import services as audit
from apps.audit.services import AuditTarget
from apps.core.errors import ErrorCode, ProblemError
from apps.core.stores import state_redis
from apps.xtream_api import ratelimit

__all__ = ["Lockout", "LockoutKind", "list_lockouts", "unlock"]

#: The most lockouts the page lists (an attack can make many; the newest come first).
LIMIT: Final = 500
_SCAN_COUNT: Final = 1000
_DIGEST: Final = re.compile(r"^[0-9a-f]{32}$")


class LockoutKind(StrEnum):
    SIGN_IN = "sign_in"
    SECOND_FACTOR = "second_factor"
    CONFIRMATION = "confirmation"
    XTREAM_LOGIN = "xtream_login"
    XTREAM_ADDRESS = "xtream_address"


@dataclass(frozen=True, slots=True)
class Lockout:
    id: str
    kind: LockoutKind
    failures: int
    until: datetime | None
    login: str = ""
    ip: str = ""
    #: An Xtream login's network (`203.0.113.0`), or "*" for its all-networks limit.
    network: str = ""
    account: User | None = field(default=None, compare=False)


# --- Listing --------------------------------------------------------------------------------------


def list_lockouts(*, now: datetime, customer: User | None = None) -> list[Lockout]:
    """Every current lockout, newest first, or those of one customer."""
    found = [
        *_sign_ins(now, customer),
        *_factors(now, customer),
        *_xtream_logins(now, customer),
        *([] if customer is not None else _xtream_addresses(now)),
    ]
    found.sort(key=lambda item: item.until or now, reverse=True)
    return found[:LIMIT]


def _logins_of(user: User) -> set[str]:
    """What a customer may type to sign in (axes records it as typed, any case)."""
    return {value.casefold() for value in (user.username, user.email, user.phone) if value}


def _sign_ins(now: datetime, customer: User | None) -> list[Lockout]:
    limit = int(settings.AXES_FAILURE_LIMIT)
    rows = list(
        AccessAttempt.objects.filter(failures_since_start__gte=limit).order_by("-attempt_time")[
            : LIMIT * 2
        ]
    )
    if customer is not None:
        logins = _logins_of(customer)
        rows = [row for row in rows if (row.username or "").casefold() in logins]
    accounts = _accounts_by_login(row.username or "" for row in rows)
    found = []
    for row in rows:
        until = row.attempt_time + cool_off_for(row.failures_since_start)
        if until <= now:
            continue
        found.append(
            Lockout(
                id=f"{LockoutKind.SIGN_IN}:{row.pk}",
                kind=LockoutKind.SIGN_IN,
                failures=row.failures_since_start,
                until=until,
                login=row.username or "",
                ip=row.ip_address or "",
                account=accounts.get((row.username or "").casefold()),
            )
        )
    return found


def _accounts_by_login(logins: Iterable[str]) -> dict[str, User]:
    wanted = {login.casefold() for login in logins if login}
    if not wanted:
        return {}
    users = User.objects.annotate(login_name=Lower("username"), login_email=Lower("email")).filter(
        Q(login_name__in=wanted) | Q(login_email__in=wanted) | Q(phone__in=wanted)
    )
    by_login: dict[str, User] = {}
    for user in users:
        for value in (user.username, user.email, user.phone):
            if value and value.casefold() in wanted:
                by_login.setdefault(value.casefold(), user)
    return by_login


def _scan(pattern: str) -> Iterator[tuple[str, int, int]]:
    """(key, count, seconds left) of the redis-state keys matching `pattern`."""
    try:
        client = state_redis()
        keys = [
            key.decode() if isinstance(key, bytes) else str(key)
            for key in client.scan_iter(match=pattern, count=_SCAN_COUNT)
        ][: LIMIT * 4]
        if not keys:
            return
        pipe = client.pipeline(transaction=False)
        for key in keys:
            pipe.get(key)
            pipe.ttl(key)
        results = pipe.execute()
    except redis.RedisError:
        return
    for index, key in enumerate(keys):
        raw, ttl = results[index * 2], results[index * 2 + 1]
        try:
            count = int(cast("bytes", raw)) if raw is not None else 0
        except ValueError:
            continue
        yield key, count, int(ttl) if isinstance(ttl, int) and ttl > 0 else 0


def _factors(now: datetime, customer: User | None) -> list[Lockout]:
    counters = (
        (LockoutKind.SECOND_FACTOR, auth.USER_MFA_FAILURES_PREFIX, auth.USER_MFA_FAILURES),
        (LockoutKind.CONFIRMATION, stepup.FAIL_PREFIX, stepup.FAIL_LIMIT),
    )
    found: list[tuple[LockoutKind, UUID, int, int]] = []
    for kind, prefix, limit in counters:
        for key, count, ttl in _scan(prefix + "*"):
            user_id = _uuid(key.removeprefix(prefix))
            if user_id is None or count < limit:
                continue
            if customer is not None and user_id != customer.pk:
                continue
            found.append((kind, user_id, count, ttl))
    users = {user.pk: user for user in User.objects.filter(pk__in={item[1] for item in found})}
    return [
        Lockout(
            id=f"{kind}:{user_id}",
            kind=kind,
            failures=count,
            until=now + timedelta(seconds=ttl) if ttl else None,
            login=users[user_id].username if user_id in users else "",
            account=users.get(user_id),
        )
        for kind, user_id, count, ttl in found
    ]


def _xtream_logins(now: datetime, customer: User | None) -> list[Lockout]:
    per_network = ratelimit.username_limit()
    rows: list[tuple[str, str, int, int]] = []  # (digest, network, count, ttl)
    for key, count, ttl in _scan(ratelimit.USER_PREFIX + "*"):
        digest, _, network = key.removeprefix(ratelimit.USER_PREFIX).partition(":")
        if _DIGEST.match(digest) and count >= per_network:
            rows.append((digest, network, count, ttl))
    for key, count, ttl in _scan(ratelimit.USER_ALL_PREFIX + "*"):
        digest = key.removeprefix(ratelimit.USER_ALL_PREFIX)
        if _DIGEST.match(digest) and count >= per_network * ratelimit.USERNAME_SPREAD:
            rows.append((digest, "*", count, ttl))
    if not rows:
        return []
    logins = _tv_logins({row[0] for row in rows}, customer)
    found = []
    for digest, network, count, ttl in rows:
        login = logins.get(digest)
        if customer is not None and login is None:
            continue
        found.append(
            Lockout(
                id=f"{LockoutKind.XTREAM_LOGIN}:{digest}:{network}",
                kind=LockoutKind.XTREAM_LOGIN,
                failures=count,
                until=now + timedelta(seconds=ttl) if ttl else None,
                login=login.username if login is not None else "",
                network=network,
                account=login.device.user if login is not None else None,
            )
        )
    return found


def _tv_logins(digests: set[str], customer: User | None) -> dict[str, XtreamCredential]:
    """Our TV logins among the paused digests (an unknown one is someone's guess)."""
    credentials = XtreamCredential.objects.select_related("device__user").only(
        "username", "device__user__id", "device__user__username", "device__user__name",
        "device__user__is_staff",
    )  # fmt: skip
    if customer is not None:
        credentials = credentials.filter(device__user=customer)
    found: dict[str, XtreamCredential] = {}
    for credential in credentials.iterator(chunk_size=2000):
        digest = ratelimit.digest(credential.username)
        if digest in digests:
            found[digest] = credential
    return found


def _xtream_addresses(now: datetime) -> list[Lockout]:
    found = []
    for key, strikes, ttl in _scan(ratelimit.BLOCK_PREFIX + "*"):
        address = key.removeprefix(ratelimit.BLOCK_PREFIX)
        if _ip(address) is None:
            continue
        found.append(
            Lockout(
                id=f"{LockoutKind.XTREAM_ADDRESS}:{address}",
                kind=LockoutKind.XTREAM_ADDRESS,
                failures=strikes,
                until=now + timedelta(seconds=ttl) if ttl else None,
                ip=address,
            )
        )
    return found


# --- Unlocking ------------------------------------------------------------------------------------


def _not_found() -> ProblemError:
    return ProblemError(ErrorCode.NOT_FOUND, "No such lockout: it may have ended already.")


def _uuid(value: str) -> UUID | None:
    try:
        return UUID(value)
    except ValueError:
        return None


def _ip(value: str) -> str | None:
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return None


def account_of(lockout_id: str) -> User | None:
    """The account a lockout belongs to, when it is one (for the admins.manage check)."""
    kind, _, rest = lockout_id.partition(":")
    if kind == LockoutKind.SIGN_IN and rest.isdigit():
        row = AccessAttempt.objects.filter(pk=int(rest)).first()
        if row is None:
            return None
        return _accounts_by_login([row.username or ""]).get((row.username or "").casefold())
    if kind in (LockoutKind.SECOND_FACTOR, LockoutKind.CONFIRMATION):
        user_id = _uuid(rest)
        return User.objects.filter(pk=user_id).first() if user_id else None
    if kind == LockoutKind.XTREAM_LOGIN:
        digest = rest.partition(":")[0]
        login = _tv_logins({digest}, None).get(digest)
        return login.device.user if login is not None else None
    return None


def unlock(lockout_id: str, *, actor: User | None, ip: str | None) -> LockoutKind:
    """Lift one lockout now; NOT_FOUND for an id that names nothing."""
    kind_text, _, rest = lockout_id.partition(":")
    try:
        kind = LockoutKind(kind_text)
    except ValueError:
        raise _not_found() from None
    details: dict[str, object] = {"kind": kind.value}
    if kind == LockoutKind.SIGN_IN:
        row = AccessAttempt.objects.filter(pk=int(rest)).first() if rest.isdigit() else None
        if row is None:
            raise _not_found()
        axes_reset(ip=row.ip_address, username=row.username)
        details.update(login=row.username or "", ip=row.ip_address or "")
    elif kind in (LockoutKind.SECOND_FACTOR, LockoutKind.CONFIRMATION):
        user_id = _uuid(rest)
        user = User.objects.filter(pk=user_id).first() if user_id else None
        if user is None:
            raise _not_found()
        if kind == LockoutKind.SECOND_FACTOR:
            auth.clear_mfa_failures(user)
        else:
            stepup.clear_failures(user)
        details["account"] = str(user.pk)
    elif kind == LockoutKind.XTREAM_LOGIN:
        digest, _, network = rest.partition(":")
        if not _DIGEST.match(digest) or not network:
            raise _not_found()
        ratelimit.clear_digest(digest, None if network == "*" else network)
        details["network"] = network
    else:
        address = _ip(rest)
        if address is None:
            raise _not_found()
        ratelimit.clear_ip(address)
        details["ip"] = address
    audit.record(
        "lockout.unlock",
        actor=actor,
        target=AuditTarget("lockout", lockout_id[:200]),
        after=details,
        ip=ip,
    )
    return kind
