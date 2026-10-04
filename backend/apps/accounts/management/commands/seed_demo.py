"""`make seed`: demo data for the proof of concept (idempotent).

Creates the RBAC catalogue and roles, an owner admin `admin`, the demo
categories, and about two dozen demo customers with access profiles spread
across active, expiring, expired, suspended and open-ended, each with one or
two IPTV devices and credentials.

The admin's password is printed once, here only (never logged). Re-run with
`--reset-admin-password` for a new one; MFA enrolment happens at first sign-in,
and `--reset-admin-mfa` makes the next sign-in enrol a new authenticator.
Device passwords are not printed: reset a device's credentials in the admin to
get one.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from axes.utils import reset as axes_reset
from django.core.management.base import BaseCommand, CommandParser
from django.db import transaction
from django.utils import timezone

from apps.accounts import services
from apps.accounts.models import AppHint, ConcurrencyPolicy, MaxQuality, MfaTotp, Role, User
from apps.accounts.rbac import OWNER_ROLE, sync_rbac
from apps.audit import services as audit
from apps.billing.seed import seed_billing
from apps.catalog.models import Category, CategoryKind

ADMIN_USERNAME = "admin"
ADMIN_EMAIL = "admin@example.com"
DEMO_EMAIL_DOMAIN = "demo.smart-iptv.test"

# (kind, slug, English, Arabic, sort)
CATEGORIES: tuple[tuple[CategoryKind, str, str, str, int], ...] = (
    (CategoryKind.VOD, "action", "Action", "أكشن", 10),
    (CategoryKind.VOD, "comedy", "Comedy", "كوميدي", 20),
    (CategoryKind.VOD, "drama", "Drama", "دراما", 30),
    (CategoryKind.VOD, "science-fiction", "Science Fiction", "خيال علمي", 40),
    (CategoryKind.VOD, "kids", "Kids", "أطفال", 50),
    (CategoryKind.VOD, "documentaries", "Documentaries", "وثائقيات", 60),
    (CategoryKind.SERIES, "drama-series", "Drama Series", "مسلسلات درامية", 10),
    (CategoryKind.SERIES, "kids-series", "Kids Series", "مسلسلات أطفال", 20),
    (CategoryKind.LIVE, "news", "News", "أخبار", 10),
    (CategoryKind.LIVE, "sports", "Sports", "رياضة", 20),
)


@dataclass(frozen=True, slots=True)
class DemoCustomer:
    key: str
    name: str
    phone: str
    locale: str
    # Days from now until access ends; None = no end. Negative = already ended.
    days_left: float | None
    devices: tuple[tuple[str, AppHint], ...]
    max_streams: int = 1
    max_devices: int = 2
    max_quality: int = MaxQuality.FHD
    policy: str = ConcurrencyPolicy.REJECT
    allow_live: bool = True
    categories: tuple[str, ...] = ()  # slugs; empty = every category
    suspended: bool = False
    notes: str = ""


_TV = ("Living room TV", AppHint.TIVIMATE)
_PHONE = ("Phone", AppHint.SMARTERS)
_BOX = ("Android box", AppHint.XCIPTV)
_TABLET = ("Tablet", AppHint.IBO)

DEMO_CUSTOMERS: tuple[DemoCustomer, ...] = (
    DemoCustomer("mohammed.harbi", "محمد الحربي", "+966501000001", "ar", 45, (_TV, _PHONE),
                 max_streams=2, max_devices=3),
    DemoCustomer("sara.otaibi", "سارة العتيبي", "+966501000002", "ar", 30, (_TV,)),
    DemoCustomer("abdullah.qahtani", "عبدالله القحطاني", "+966501000003", "ar", 60, (_BOX, _PHONE),
                 max_streams=2, max_devices=3, max_quality=MaxQuality.UHD),
    DemoCustomer("noura.shammari", "نورة الشمري", "+966501000004", "ar", 22, (_TABLET,),
                 categories=("kids", "kids-series"), allow_live=False,
                 notes="Kids profile only."),
    DemoCustomer("khalid.dossari", "خالد الدوسري", "+966501000005", "ar", 90, (_TV, _BOX),
                 max_streams=4, max_devices=5, max_quality=MaxQuality.UHD,
                 policy=ConcurrencyPolicy.KICK_OLDEST),
    DemoCustomer("fatimah.zahrani", "فاطمة الزهراني", "+966501000006", "ar", 15, (_PHONE,),
                 max_quality=MaxQuality.HD),
    DemoCustomer("omar.ghamdi", "عمر الغامدي", "+966501000007", "ar", 33, (_TV,),
                 categories=("news", "sports"), notes="Live sports and news package."),
    DemoCustomer("reem.mutairi", "ريم المطيري", "+966501000008", "ar", 41, (_TV, _TABLET),
                 max_streams=2),
    DemoCustomer("james.carter", "James Carter", "+966501000009", "en", 28, (_BOX,)),
    DemoCustomer("emily.walsh", "Emily Walsh", "+966501000010", "en", 52, (_TV, _PHONE),
                 max_streams=2, max_devices=3),
    DemoCustomer("ahmed.siddiqui", "Ahmed Siddiqui", "+966501000011", "en", 19, (_PHONE,),
                 max_quality=MaxQuality.HD),
    DemoCustomer("lina.haddad", "Lina Haddad", "+966501000012", "en", 75, (_TV,),
                 categories=("drama", "drama-series", "comedy")),
    # Expiring within 7 days.
    DemoCustomer("yousef.anazi", "يوسف العنزي", "+966501000013", "ar", 1, (_TV,)),
    DemoCustomer("hessa.subaie", "حصة السبيعي", "+966501000014", "ar", 3, (_PHONE, _TABLET)),
    DemoCustomer("daniel.reyes", "Daniel Reyes", "+966501000015", "en", 6, (_BOX,)),
    # Already ended.
    DemoCustomer("faisal.juhani", "فيصل الجهني", "+966501000016", "ar", -2, (_TV,)),
    DemoCustomer("maha.rashidi", "مها الرشيدي", "+966501000017", "ar", -10, (_PHONE,)),
    DemoCustomer("sophie.martin", "Sophie Martin", "+966501000018", "en", -40, (_TV,)),
    # Suspended by an admin.
    DemoCustomer("turki.shahri", "تركي الشهري", "+966501000019", "ar", 25, (_TV, _BOX),
                 suspended=True, notes="Suspended: shared credentials (demo)."),
    DemoCustomer("ryan.patel", "Ryan Patel", "+966501000020", "en", 12, (_PHONE,),
                 suspended=True),
    # No end date.
    DemoCustomer("staff.lounge", "Staff Lounge TV", "+966501000021", "en", None, (_TV,),
                 max_quality=MaxQuality.UHD, notes="In-house screen, no expiry."),
    DemoCustomer("ali.malki", "علي المالكي", "+966501000022", "ar", 8, (_TV,)),
    DemoCustomer("huda.shehri", "هدى الشهري", "+966501000023", "ar", 120, (_PHONE,),
                 max_quality=MaxQuality.SD),
    DemoCustomer("mark.evans", "Mark Evans", "+966501000024", "en", 37, (_TV, _PHONE)),
)  # fmt: skip


class Command(BaseCommand):
    help = "Load idempotent demo data: roles, the owner admin, categories and customers."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--reset-admin-password",
            action="store_true",
            help="Give the existing admin a new password (printed once) and clear its lockouts.",
        )
        parser.add_argument(
            "--reset-admin-mfa",
            action="store_true",
            help="Remove the admin's authenticator: the next sign-in enrols a new one.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        sync_rbac()
        self._admin(reset_password=options["reset_admin_password"])
        if options["reset_admin_mfa"]:
            self._reset_admin_mfa()
        categories = self._categories()
        created = self._customers(categories)
        expired = services.process_expired_access()
        self.stdout.write(
            f"Demo data ready: {len(categories)} categories, {created} new customers "
            f"({len(DEMO_CUSTOMERS)} in total), {expired} access periods marked expired."
        )
        self.stdout.write(seed_billing())  # plans and subscriptions (B1, ADR-0012)

    def _admin(self, *, reset_password: bool) -> None:
        user = User.objects.filter(username=ADMIN_USERNAME).first()
        if user is not None and not reset_password:
            self.stdout.write(
                f"Admin '{ADMIN_USERNAME}' already exists; "
                "re-run with --reset-admin-password for a new password."
            )
            return
        password = services.generate_admin_password()
        with transaction.atomic():
            created = user is None
            if user is None:
                user = User(username=ADMIN_USERNAME, email=ADMIN_EMAIL, name="Owner", is_staff=True)
            user.set_password(password)
            user.save()
            user.roles.add(Role.objects.get(name=OWNER_ROLE))
            if created:
                audit.record(
                    "admin.create",
                    actor=None,
                    target=user,
                    after=services.admin_snapshot(user, [OWNER_ROLE]),
                )
            else:
                audit.record("admin.password_reset", actor=None, target=user)
        action = "created" if created else "password reset"
        axes_reset(username=ADMIN_USERNAME)
        self.stdout.write(self.style.SUCCESS(f"Admin '{ADMIN_USERNAME}' {action}."))
        self.stdout.write(f"  Password (shown once, store it now): {password}")
        self.stdout.write("  At first sign-in the admin enrols an authenticator app (TOTP MFA).")

    def _reset_admin_mfa(self) -> None:
        with transaction.atomic():
            user = User.objects.select_for_update().get(username=ADMIN_USERNAME)
            MfaTotp.objects.filter(user=user).delete()
            user.mfa_enabled = False
            user.save(update_fields=["mfa_enabled", "updated_at"])
            audit.record("admin.mfa_reset", actor=None, target=user)
        self.stdout.write(
            self.style.SUCCESS(
                f"Admin '{ADMIN_USERNAME}' MFA reset: the next sign-in enrols a new authenticator."
            )
        )

    def _categories(self) -> dict[str, Category]:
        by_slug: dict[str, Category] = {}
        for kind, slug, name_en, name_ar, sort in CATEGORIES:
            category, _created = Category.objects.get_or_create(
                kind=kind,
                slug=slug,
                defaults={"name_en": name_en, "name_ar": name_ar, "sort": sort},
            )
            by_slug[slug] = category
        return by_slug

    def _customers(self, categories: dict[str, Category]) -> int:
        now = timezone.now()
        existing = set(
            User.objects.filter(email__endswith=f"@{DEMO_EMAIL_DOMAIN}").values_list(
                "email", flat=True
            )
        )
        created = 0
        for demo in DEMO_CUSTOMERS:
            email = f"{demo.key}@{DEMO_EMAIL_DOMAIN}"
            if email in existing:
                continue
            with transaction.atomic():
                self._customer(demo, email, categories, now)
            created += 1
        return created

    def _customer(
        self,
        demo: DemoCustomer,
        email: str,
        categories: dict[str, Category],
        now: datetime,
    ) -> None:
        expires_at = None if demo.days_left is None else now + timedelta(days=demo.days_left)
        first_name, first_hint = demo.devices[0]
        user, _issued = services.create_customer(
            {
                "name": demo.name,
                "email": email,
                "phone": demo.phone,
                "locale": demo.locale,
                "notes": demo.notes,
            },
            access={
                "expires_at": expires_at,
                "max_streams": demo.max_streams,
                "max_devices": demo.max_devices,
                "max_quality": demo.max_quality,
                "concurrency_policy": demo.policy,
                "allow_live": demo.allow_live,
                "category_ids": [categories[slug].pk for slug in demo.categories],
            },
            device={"name": first_name, "app_hint": first_hint},
            actor=None,
        )
        for name, hint in demo.devices[1:]:
            services.create_device_credential(user, name=name, app_hint=hint, actor=None)
        if demo.suspended:
            services.suspend_customer(user, actor=None, reason="Demo data")
