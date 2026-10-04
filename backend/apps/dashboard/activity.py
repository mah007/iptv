"""The activity feed (SPEC §8.3 Dashboard "recent activity", customer detail "Audit").

Audit entries with a readable label for their target, newest first. The dashboard
shows the operational ones (sign-ins and sign-outs stay in the audit log); a customer's
feed is everything done to the customer, their devices, access rules and sessions.
Labels are resolved per target type, one query per type on the page, so a page costs
the same number of queries whatever it holds.
"""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from django.db.models import CharField, Q, QuerySet
from django.db.models.functions import Cast

from apps.accounts.models import AccessRule, Device, Role, User
from apps.audit.models import AuditLog
from apps.billing.models import Invoice, Payment, Plan, Subscription
from apps.catalog.models import Category, Collection, Movie, Series
from apps.library.models import Library
from apps.notifications.models import NotificationTemplate
from apps.playback.models import PlaybackSession

#: Actions the dashboard leaves to the audit log.
QUIET_ACTION_PREFIXES = ("auth.",)


@dataclass(frozen=True, slots=True)
class Subject:
    """What an entry is about: a label to show and the customer it belongs to, if any."""

    label: str
    customer_id: str | None = None
    customer_name: str = ""


type Resolver = Callable[[list[str]], dict[str, Subject]]


def _uuids(ids: Iterable[str]) -> list[UUID]:
    valid = []
    for value in ids:
        try:
            valid.append(UUID(value))
        except ValueError:
            continue
    return valid


def _customer(user: User | None) -> tuple[str | None, str]:
    if user is None or user.is_staff:
        return None, ""
    return str(user.pk), user.name or user.username


def _users(ids: list[str]) -> dict[str, Subject]:
    subjects = {}
    for user in User.objects.filter(pk__in=_uuids(ids)).only("id", "name", "username", "is_staff"):
        customer_id, customer_name = _customer(user)
        subjects[str(user.pk)] = Subject(user.name or user.username, customer_id, customer_name)
    return subjects


def _devices(ids: list[str]) -> dict[str, Subject]:
    rows = Device.objects.filter(pk__in=_uuids(ids)).select_related("user")
    subjects = {}
    for device in rows:
        customer_id, customer_name = _customer(device.user)
        subjects[str(device.pk)] = Subject(device.name, customer_id, customer_name)
    return subjects


def _rules(ids: list[str]) -> dict[str, Subject]:
    subjects = {}
    for rule in AccessRule.objects.filter(pk__in=_uuids(ids)).select_related("user"):
        customer_id, customer_name = _customer(rule.user)
        subjects[str(rule.pk)] = Subject(rule.value, customer_id, customer_name)
    return subjects


def _sessions(ids: list[str]) -> dict[str, Subject]:
    subjects = {}
    for session in PlaybackSession.objects.filter(pk__in=_uuids(ids)).select_related("user"):
        customer_id, customer_name = _customer(session.user)
        subjects[str(session.pk)] = Subject(session.title_name, customer_id, customer_name)
    return subjects


def _subscriptions(ids: list[str]) -> dict[str, Subject]:
    subjects = {}
    rows = Subscription.objects.filter(pk__in=_uuids(ids)).select_related("user", "plan")
    for subscription in rows:
        customer_id, customer_name = _customer(subscription.user)
        subjects[str(subscription.pk)] = Subject(
            subscription.plan.name_en, customer_id, customer_name
        )
    return subjects


def _payments(ids: list[str]) -> dict[str, Subject]:
    subjects = {}
    rows = Payment.objects.filter(pk__in=_uuids(ids)).select_related("user", "plan")
    for payment in rows:
        customer_id, customer_name = _customer(payment.user)
        label = payment.plan.name_en if payment.plan is not None else ""
        subjects[str(payment.pk)] = Subject(label, customer_id, customer_name)
    return subjects


def _invoices(ids: list[str]) -> dict[str, Subject]:
    subjects = {}
    for invoice in Invoice.objects.filter(pk__in=_uuids(ids)).select_related("user"):
        customer_id, customer_name = _customer(invoice.user)
        subjects[str(invoice.pk)] = Subject(invoice.number or "", customer_id, customer_name)
    return subjects


def _templates(ids: list[str]) -> dict[str, Subject]:
    rows = NotificationTemplate.objects.filter(pk__in=_uuids(ids)).values_list(
        "pk", "key", "locale"
    )
    return {str(pk): Subject(f"{key} ({locale})") for pk, key, locale in rows}


def _named(model: Any, attribute: str) -> Resolver:
    def resolve(ids: list[str]) -> dict[str, Subject]:
        rows = model.objects.filter(pk__in=_uuids(ids)).values_list("pk", attribute)
        return {str(pk): Subject(str(value)) for pk, value in rows}

    return resolve


def _keys(ids: list[str]) -> dict[str, Subject]:
    """Targets whose id is already readable (setting keys, category kinds)."""
    return {value: Subject(value) for value in ids}


RESOLVERS: Mapping[str, Resolver] = {
    "accounts.user": _users,
    "accounts.device": _devices,
    "accounts.accessrule": _rules,
    "accounts.role": _named(Role, "name"),
    "playback.playbacksession": _sessions,
    "catalog.movie": _named(Movie, "title"),
    "catalog.series": _named(Series, "title"),
    "library.library": _named(Library, "name"),
    "core.setting": _keys,
    "catalog.collection": _named(Collection, "name_en"),
    "billing.plan": _named(Plan, "name_en"),
    "billing.subscription": _subscriptions,
    "billing.payment": _payments,
    "billing.invoice": _invoices,
    "notifications.notificationtemplate": _templates,
}


def _category_resolver(ids: list[str]) -> dict[str, Subject]:
    # A category id, or a kind for reorders (`catalog.category`, "vod").
    subjects = _keys([value for value in ids if not _uuids([value])])
    subjects.update(_named(Category, "name_en")(ids))
    return subjects


def resolver_for(target_type: str) -> Resolver | None:
    if target_type == "catalog.category":
        return _category_resolver
    return RESOLVERS.get(target_type)


def subjects(entries: Iterable[AuditLog]) -> dict[tuple[str, str], Subject]:
    """Labels for the targets of `entries`, keyed by (target_type, target_id)."""
    by_type: dict[str, set[str]] = {}
    for entry in entries:
        if entry.target_type and entry.target_id:
            by_type.setdefault(entry.target_type, set()).add(entry.target_id)
    resolved: dict[tuple[str, str], Subject] = {}
    for target_type, ids in by_type.items():
        resolve = resolver_for(target_type)
        if resolve is None:
            continue
        for target_id, subject in resolve(sorted(ids)).items():
            resolved[target_type, target_id] = subject
    return resolved


def _ids_of(queryset: QuerySet[Any]) -> QuerySet[Any]:
    ids: QuerySet[Any] = queryset.annotate(text_id=Cast("pk", CharField())).values("text_id")
    return ids


def customer_scope(user_id: UUID) -> Q:
    """Entries about the customer, their devices, access rules (also deleted ones),
    sessions, subscriptions, payments and invoices."""
    text = str(user_id)
    rules = Q(target_type="accounts.accessrule") & (
        Q(after__user_id=text) | Q(before__user_id=text)
    )
    devices = Q(
        target_type="accounts.device",
        target_id__in=_ids_of(Device.objects.filter(user_id=user_id)),
    )
    sessions = Q(
        target_type="playback.playbacksession",
        target_id__in=_ids_of(PlaybackSession.objects.filter(user_id=user_id)),
    )
    billing = (
        Q(
            target_type="billing.subscription",
            target_id__in=_ids_of(Subscription.objects.filter(user_id=user_id)),
        )
        | Q(
            target_type="billing.payment",
            target_id__in=_ids_of(Payment.objects.filter(user_id=user_id)),
        )
        | Q(
            target_type="billing.invoice",
            target_id__in=_ids_of(Invoice.objects.filter(user_id=user_id)),
        )
    )
    return Q(target_type="accounts.user", target_id=text) | devices | rules | sessions | billing


def feed(*, customer: UUID | None = None) -> QuerySet[AuditLog]:
    entries = AuditLog.objects.select_related("actor").order_by("-at", "-id")
    if customer is not None:
        return entries.filter(customer_scope(customer))
    quiet = Q()
    for prefix in QUIET_ACTION_PREFIXES:
        quiet |= Q(action__startswith=prefix)
    return entries.exclude(quiet)
