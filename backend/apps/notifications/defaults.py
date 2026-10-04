# Arabic text right after a \n escape trips ruff's confusable-letter check.
# ruff: noqa: RUF001
"""Default notification templates, in English and Arabic (SPEC §7.7).

An admin's `NotificationTemplate` row replaces the default of one (event, channel,
locale). Every template can use the common variables (`service_name`, `name`,
`support_email`, `portal_url`) plus its event's variables listed in `EVENTS`.
`body_html` is the message's content; `rendering.wrap_html` puts it in the
branded email layout.
"""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

COMMON_VARIABLES: Final = ("service_name", "name", "support_email", "portal_url")


@dataclass(frozen=True, slots=True)
class EventDef:
    key: str
    description: str
    variables: tuple[str, ...]
    # Sample values for previews and test sends.
    sample: dict[str, object]


@dataclass(frozen=True, slots=True)
class DefaultTemplate:
    subject: str
    body_text: str
    body_html: str


_EVENTS: tuple[EventDef, ...] = (
    EventDef(
        "account_activated",
        "A customer's first subscription (or trial) starts.",
        ("plan_name", "ends_at", "server_url", "usernames"),
        {
            "plan_name": "Premium",
            "ends_at": "2026-11-03 21:00",
            "server_url": "https://tv.example.com",
            "usernames": ["sar-k3p9qa"],
        },
    ),
    EventDef(
        "subscription_renewed",
        "A subscription was extended or renewed.",
        ("plan_name", "ends_at"),
        {"plan_name": "Premium", "ends_at": "2026-12-03 21:00"},
    ),
    EventDef(
        "payment_succeeded",
        "A payment went through.",
        ("amount", "plan_name", "invoice_number", "ends_at"),
        {
            "amount": "79.00 SAR",
            "plan_name": "Premium",
            "invoice_number": "INV-2026-000042",
            "ends_at": "2026-12-03 21:00",
        },
    ),
    EventDef(
        "payment_failed",
        "A payment was declined or failed.",
        ("amount", "plan_name", "reason"),
        {"amount": "79.00 SAR", "plan_name": "Premium", "reason": "Card declined"},
    ),
    EventDef(
        "payment_refunded",
        "A payment was refunded, in full or in part.",
        ("amount", "invoice_number"),
        {"amount": "79.00 SAR", "invoice_number": "INV-2026-000042"},
    ),
    EventDef(
        "expiring_7d",
        "Seven days before a subscription ends.",
        ("plan_name", "ends_at"),
        {"plan_name": "Premium", "ends_at": "2026-11-03 21:00"},
    ),
    EventDef(
        "expiring_1d",
        "One day before a subscription ends.",
        ("plan_name", "ends_at"),
        {"plan_name": "Premium", "ends_at": "2026-11-03 21:00"},
    ),
    EventDef(
        "expired",
        "A subscription ended (after its grace period).",
        ("plan_name",),
        {"plan_name": "Premium"},
    ),
    EventDef(
        "new_device",
        "A new device signed in to the account.",
        ("device_name", "app"),
        {"device_name": "Living room TV", "app": "TiviMate"},
    ),
    EventDef(
        "device_limit_reached",
        "The account reached its device limit.",
        ("max_devices",),
        {"max_devices": 3},
    ),
    EventDef(
        "credentials_reset",
        "A device's IPTV app credentials were reset.",
        ("device_name", "username", "server_url"),
        {
            "device_name": "Living room TV",
            "username": "sar-k3p9qa",
            "server_url": "https://tv.example.com",
        },
    ),
    EventDef(
        "password_reset",
        "A customer asked to reset their portal password (sent at once, never stored).",
        ("link_url", "expires_at"),
        {
            "link_url": "https://app.example.com/reset-password?uid=MQ&token=sample",
            "expires_at": "2026-11-03 21:00",
        },
    ),
    EventDef(
        "password_invite",
        "An admin invited a customer to set their portal password (sent at once, never stored).",
        ("link_url", "expires_at"),
        {
            "link_url": "https://app.example.com/reset-password?uid=MQ&token=sample&welcome=1",
            "expires_at": "2026-11-10 21:00",
        },
    ),
    EventDef(
        "suspicious_activity",
        "Sent to admins: an account shows signs of credential sharing.",
        ("customer", "reason"),
        {"customer": "cus-a1b2c3d4", "reason": "Streams from 3 countries within an hour"},
    ),
)
EVENTS: Final = MappingProxyType({event.key: event for event in _EVENTS})
#: Events whose message carries a secret (a password link): sent at once by
#: `services.send_password_link`, never stored in the outbox, never retried.
SECRET_EVENTS: Final = frozenset({"password_reset", "password_invite"})

_FOOTER_EN = "Questions? Write to us at {{ support_email|default:'our support team' }}."
_FOOTER_AR = "لأي استفسار راسلنا على {{ support_email|default:'فريق الدعم' }}."

# (event, locale) -> template
_DEFAULTS: dict[tuple[str, str], DefaultTemplate] = {
    ("account_activated", "en"): DefaultTemplate(
        "Your {{ service_name }} subscription is active",
        "Hello {{ name }},\n\nYour {{ plan_name }} subscription is active until {{ ends_at }}.\n\n"
        "To watch on a TV or phone, open an IPTV app (IPTV Smarters, TiviMate, ...) and "
        'choose "Xtream Codes":\n- Server: {{ server_url }}\n'
        "{% for username in usernames %}- Username: {{ username }}\n{% endfor %}"
        "- Password: the one you were given when the device was added.\n\n"
        "Manage your account: {{ portal_url }}\n\n" + _FOOTER_EN,
        "<p>Hello {{ name }},</p><p>Your <strong>{{ plan_name }}</strong> subscription is "
        "active until <strong>{{ ends_at }}</strong>.</p><p>To watch on a TV or phone, open an "
        "IPTV app (IPTV Smarters, TiviMate, ...) and choose <em>Xtream Codes</em>:</p><ul>"
        "<li>Server: <code>{{ server_url }}</code></li>{% for username in usernames %}"
        "<li>Username: <code>{{ username }}</code></li>{% endfor %}<li>Password: the one you "
        'were given when the device was added.</li></ul><p><a href="{{ portal_url }}">'
        "Manage your account</a></p><p>" + _FOOTER_EN + "</p>",
    ),
    ("account_activated", "ar"): DefaultTemplate(
        "تم تفعيل اشتراكك في {{ service_name }}",
        "مرحباً {{ name }}،\n\nاشتراكك في باقة {{ plan_name }} مفعّل حتى {{ ends_at }}.\n\n"
        "للمشاهدة على التلفاز أو الجوال افتح تطبيق IPTV (مثل IPTV Smarters أو TiviMate) "
        'واختر "Xtream Codes":\n- الخادم: {{ server_url }}\n'
        "{% for username in usernames %}- اسم المستخدم: {{ username }}\n{% endfor %}"
        "- كلمة المرور: التي استلمتها عند إضافة الجهاز.\n\n"
        "إدارة حسابك: {{ portal_url }}\n\n" + _FOOTER_AR,
        "<p>مرحباً {{ name }}،</p><p>اشتراكك في باقة <strong>{{ plan_name }}</strong> مفعّل حتى "
        "<strong>{{ ends_at }}</strong>.</p><p>للمشاهدة على التلفاز أو الجوال افتح تطبيق IPTV "
        "(مثل IPTV Smarters أو TiviMate) واختر <em>Xtream Codes</em>:</p><ul>"
        "<li>الخادم: <code>{{ server_url }}</code></li>{% for username in usernames %}"
        "<li>اسم المستخدم: <code>{{ username }}</code></li>{% endfor %}<li>كلمة المرور: التي "
        'استلمتها عند إضافة الجهاز.</li></ul><p><a href="{{ portal_url }}">إدارة حسابك</a></p>'
        "<p>" + _FOOTER_AR + "</p>",
    ),
    ("subscription_renewed", "en"): DefaultTemplate(
        "Your {{ service_name }} subscription was renewed",
        "Hello {{ name }},\n\nYour {{ plan_name }} subscription now runs until {{ ends_at }}.\n\n"
        + _FOOTER_EN,
        "<p>Hello {{ name }},</p><p>Your <strong>{{ plan_name }}</strong> subscription now runs "
        "until <strong>{{ ends_at }}</strong>.</p><p>" + _FOOTER_EN + "</p>",
    ),
    ("subscription_renewed", "ar"): DefaultTemplate(
        "تم تجديد اشتراكك في {{ service_name }}",
        "مرحباً {{ name }}،\n\nأصبح اشتراكك في باقة {{ plan_name }} سارياً حتى {{ ends_at }}.\n\n"
        + _FOOTER_AR,
        "<p>مرحباً {{ name }}،</p><p>أصبح اشتراكك في باقة <strong>{{ plan_name }}</strong> سارياً "
        "حتى <strong>{{ ends_at }}</strong>.</p><p>" + _FOOTER_AR + "</p>",
    ),
    ("payment_succeeded", "en"): DefaultTemplate(
        "Payment received: {{ amount }}",
        "Hello {{ name }},\n\nWe received your payment of {{ amount }} for {{ plan_name }}. "
        "Invoice {{ invoice_number }} is in your account.\nYour subscription runs until "
        "{{ ends_at }}.\n\n" + _FOOTER_EN,
        "<p>Hello {{ name }},</p><p>We received your payment of <strong>{{ amount }}</strong> "
        "for {{ plan_name }}. Invoice <strong>{{ invoice_number }}</strong> is in your "
        "account.</p><p>Your subscription runs until <strong>{{ ends_at }}</strong>.</p><p>"
        + _FOOTER_EN
        + "</p>",
    ),
    ("payment_succeeded", "ar"): DefaultTemplate(
        "تم استلام دفعتك: {{ amount }}",
        "مرحباً {{ name }}،\n\nاستلمنا دفعتك بمبلغ {{ amount }} لباقة {{ plan_name }}. "
        "الفاتورة رقم {{ invoice_number }} متاحة في حسابك.\nاشتراكك ساري حتى {{ ends_at }}.\n\n"
        + _FOOTER_AR,
        "<p>مرحباً {{ name }}،</p><p>استلمنا دفعتك بمبلغ <strong>{{ amount }}</strong> لباقة "
        "{{ plan_name }}. الفاتورة رقم <strong>{{ invoice_number }}</strong> متاحة في حسابك."
        "</p><p>اشتراكك ساري حتى <strong>{{ ends_at }}</strong>.</p><p>" + _FOOTER_AR + "</p>",
    ),
    ("payment_failed", "en"): DefaultTemplate(
        "Your payment did not go through",
        "Hello {{ name }},\n\nYour payment of {{ amount }} for {{ plan_name }} did not go "
        "through ({{ reason }}). You can try again from your account: {{ portal_url }}\n\n"
        + _FOOTER_EN,
        "<p>Hello {{ name }},</p><p>Your payment of <strong>{{ amount }}</strong> for "
        '{{ plan_name }} did not go through ({{ reason }}).</p><p><a href="{{ portal_url }}">'
        "Try again from your account</a></p><p>" + _FOOTER_EN + "</p>",
    ),
    ("payment_failed", "ar"): DefaultTemplate(
        "لم تتم عملية الدفع",
        "مرحباً {{ name }}،\n\nلم تتم دفعتك بمبلغ {{ amount }} لباقة {{ plan_name }} "
        "({{ reason }}). يمكنك المحاولة مجدداً من حسابك: {{ portal_url }}\n\n" + _FOOTER_AR,
        "<p>مرحباً {{ name }}،</p><p>لم تتم دفعتك بمبلغ <strong>{{ amount }}</strong> لباقة "
        '{{ plan_name }} ({{ reason }}).</p><p><a href="{{ portal_url }}">حاول مجدداً من '
        "حسابك</a></p><p>" + _FOOTER_AR + "</p>",
    ),
    ("payment_refunded", "en"): DefaultTemplate(
        "Refund issued: {{ amount }}",
        "Hello {{ name }},\n\nWe refunded {{ amount }} of invoice {{ invoice_number }}. "
        "Depending on your bank it can take a few days to appear.\n\n" + _FOOTER_EN,
        "<p>Hello {{ name }},</p><p>We refunded <strong>{{ amount }}</strong> of invoice "
        "{{ invoice_number }}. Depending on your bank it can take a few days to appear.</p><p>"
        + _FOOTER_EN
        + "</p>",
    ),
    ("payment_refunded", "ar"): DefaultTemplate(
        "تم استرداد مبلغ {{ amount }}",
        "مرحباً {{ name }}،\n\nأعدنا مبلغ {{ amount }} من الفاتورة رقم {{ invoice_number }}. "
        "قد يستغرق ظهوره بضعة أيام حسب البنك.\n\n" + _FOOTER_AR,
        "<p>مرحباً {{ name }}،</p><p>أعدنا مبلغ <strong>{{ amount }}</strong> من الفاتورة رقم "
        "{{ invoice_number }}. قد يستغرق ظهوره بضعة أيام حسب البنك.</p><p>" + _FOOTER_AR + "</p>",
    ),
    ("expiring_7d", "en"): DefaultTemplate(
        "Your subscription ends in 7 days",
        "Hello {{ name }},\n\nYour {{ plan_name }} subscription ends on {{ ends_at }}. Renew "
        "now to keep watching: {{ portal_url }}\n\n" + _FOOTER_EN,
        "<p>Hello {{ name }},</p><p>Your <strong>{{ plan_name }}</strong> subscription ends on "
        '<strong>{{ ends_at }}</strong>.</p><p><a href="{{ portal_url }}">Renew now</a> to '
        "keep watching.</p><p>" + _FOOTER_EN + "</p>",
    ),
    ("expiring_7d", "ar"): DefaultTemplate(
        "ينتهي اشتراكك خلال 7 أيام",
        "مرحباً {{ name }}،\n\nينتهي اشتراكك في باقة {{ plan_name }} في {{ ends_at }}. "
        "جدّد الآن لتستمر المشاهدة: {{ portal_url }}\n\n" + _FOOTER_AR,
        "<p>مرحباً {{ name }}،</p><p>ينتهي اشتراكك في باقة <strong>{{ plan_name }}</strong> في "
        '<strong>{{ ends_at }}</strong>.</p><p><a href="{{ portal_url }}">جدّد الآن</a> لتستمر '
        "المشاهدة.</p><p>" + _FOOTER_AR + "</p>",
    ),
    ("expiring_1d", "en"): DefaultTemplate(
        "Your subscription ends tomorrow",
        "Hello {{ name }},\n\nYour {{ plan_name }} subscription ends on {{ ends_at }}. Renew "
        "today to keep watching: {{ portal_url }}\n\n" + _FOOTER_EN,
        "<p>Hello {{ name }},</p><p>Your <strong>{{ plan_name }}</strong> subscription ends on "
        '<strong>{{ ends_at }}</strong>.</p><p><a href="{{ portal_url }}">Renew today</a> to '
        "keep watching.</p><p>" + _FOOTER_EN + "</p>",
    ),
    ("expiring_1d", "ar"): DefaultTemplate(
        "ينتهي اشتراكك غداً",
        "مرحباً {{ name }}،\n\nينتهي اشتراكك في باقة {{ plan_name }} في {{ ends_at }}. "
        "جدّد اليوم لتستمر المشاهدة: {{ portal_url }}\n\n" + _FOOTER_AR,
        "<p>مرحباً {{ name }}،</p><p>ينتهي اشتراكك في باقة <strong>{{ plan_name }}</strong> في "
        '<strong>{{ ends_at }}</strong>.</p><p><a href="{{ portal_url }}">جدّد اليوم</a> '
        "لتستمر المشاهدة.</p><p>" + _FOOTER_AR + "</p>",
    ),
    ("expired", "en"): DefaultTemplate(
        "Your {{ service_name }} subscription has ended",
        "Hello {{ name }},\n\nYour {{ plan_name }} subscription has ended. Renew to watch "
        "again: {{ portal_url }}\n\n" + _FOOTER_EN,
        "<p>Hello {{ name }},</p><p>Your <strong>{{ plan_name }}</strong> subscription has "
        'ended.</p><p><a href="{{ portal_url }}">Renew</a> to watch again.</p><p>'
        + _FOOTER_EN
        + "</p>",
    ),
    ("expired", "ar"): DefaultTemplate(
        "انتهى اشتراكك في {{ service_name }}",
        "مرحباً {{ name }}،\n\nانتهى اشتراكك في باقة {{ plan_name }}. جدّد لتعود للمشاهدة: "
        "{{ portal_url }}\n\n" + _FOOTER_AR,
        "<p>مرحباً {{ name }}،</p><p>انتهى اشتراكك في باقة <strong>{{ plan_name }}</strong>.</p>"
        '<p><a href="{{ portal_url }}">جدّد</a> لتعود للمشاهدة.</p><p>' + _FOOTER_AR + "</p>",
    ),
    ("new_device", "en"): DefaultTemplate(
        "A new device signed in to your account",
        "Hello {{ name }},\n\n{{ device_name }} ({{ app }}) signed in to your account. If this "
        "wasn't you, reset its credentials from your account: {{ portal_url }}\n\n" + _FOOTER_EN,
        "<p>Hello {{ name }},</p><p><strong>{{ device_name }}</strong> ({{ app }}) signed in to "
        'your account.</p><p>If this wasn\'t you, <a href="{{ portal_url }}">reset its '
        "credentials</a>.</p><p>" + _FOOTER_EN + "</p>",
    ),
    ("new_device", "ar"): DefaultTemplate(
        "جهاز جديد سجّل الدخول إلى حسابك",
        "مرحباً {{ name }}،\n\nسجّل الجهاز {{ device_name }} ({{ app }}) الدخول إلى حسابك. إن لم "
        "تكن أنت فأعد تعيين بياناته من حسابك: {{ portal_url }}\n\n" + _FOOTER_AR,
        "<p>مرحباً {{ name }}،</p><p>سجّل الجهاز <strong>{{ device_name }}</strong> ({{ app }}) "
        'الدخول إلى حسابك.</p><p>إن لم تكن أنت <a href="{{ portal_url }}">فأعد تعيين بياناته'
        "</a>.</p><p>" + _FOOTER_AR + "</p>",
    ),
    ("device_limit_reached", "en"): DefaultTemplate(
        "Your account reached its device limit",
        "Hello {{ name }},\n\nYour plan allows {{ max_devices }} devices and all of them are in "
        "use. Remove one from your account to add another: {{ portal_url }}\n\n" + _FOOTER_EN,
        "<p>Hello {{ name }},</p><p>Your plan allows {{ max_devices }} devices and all of them "
        'are in use.</p><p><a href="{{ portal_url }}">Remove one</a> to add another.</p><p>'
        + _FOOTER_EN
        + "</p>",
    ),
    ("device_limit_reached", "ar"): DefaultTemplate(
        "وصل حسابك إلى الحد الأقصى للأجهزة",
        "مرحباً {{ name }}،\n\nتسمح باقتك بـ {{ max_devices }} أجهزة وجميعها مستخدمة. احذف جهازاً "
        "من حسابك لإضافة آخر: {{ portal_url }}\n\n" + _FOOTER_AR,
        "<p>مرحباً {{ name }}،</p><p>تسمح باقتك بـ {{ max_devices }} أجهزة وجميعها مستخدمة.</p>"
        '<p><a href="{{ portal_url }}">احذف جهازاً</a> لإضافة آخر.</p><p>' + _FOOTER_AR + "</p>",
    ),
    ("credentials_reset", "en"): DefaultTemplate(
        "Your IPTV app login was reset",
        "Hello {{ name }},\n\nThe login of {{ device_name }} was reset. Server: {{ server_url }}, "
        "username: {{ username }}. Use the new password you were given.\n\n" + _FOOTER_EN,
        "<p>Hello {{ name }},</p><p>The login of <strong>{{ device_name }}</strong> was reset."
        "</p><ul><li>Server: <code>{{ server_url }}</code></li><li>Username: <code>"
        "{{ username }}</code></li></ul><p>Use the new password you were given.</p><p>"
        + _FOOTER_EN
        + "</p>",
    ),
    ("credentials_reset", "ar"): DefaultTemplate(
        "تمت إعادة تعيين بيانات دخول تطبيق IPTV",
        "مرحباً {{ name }}،\n\nتمت إعادة تعيين بيانات دخول الجهاز {{ device_name }}. الخادم: "
        "{{ server_url }}، اسم المستخدم: {{ username }}. استخدم كلمة المرور الجديدة التي "
        "استلمتها.\n\n" + _FOOTER_AR,
        "<p>مرحباً {{ name }}،</p><p>تمت إعادة تعيين بيانات دخول الجهاز <strong>{{ device_name }}"
        "</strong>.</p><ul><li>الخادم: <code>{{ server_url }}</code></li><li>اسم المستخدم: "
        "<code>{{ username }}</code></li></ul><p>استخدم كلمة المرور الجديدة التي استلمتها.</p>"
        "<p>" + _FOOTER_AR + "</p>",
    ),
    ("suspicious_activity", "en"): DefaultTemplate(
        "Suspicious activity on {{ customer }}",
        "Account {{ customer }} shows signs of credential sharing: {{ reason }}.\n"
        "Review it in the admin.",
        "<p>Account <strong>{{ customer }}</strong> shows signs of credential sharing: "
        "{{ reason }}.</p><p>Review it in the admin.</p>",
    ),
    ("suspicious_activity", "ar"): DefaultTemplate(
        "نشاط مريب على الحساب {{ customer }}",
        "تظهر على الحساب {{ customer }} مؤشرات مشاركة بيانات الدخول: {{ reason }}.\n"
        "راجعه من لوحة الإدارة.",
        "<p>تظهر على الحساب <strong>{{ customer }}</strong> مؤشرات مشاركة بيانات الدخول: "
        "{{ reason }}.</p><p>راجعه من لوحة الإدارة.</p>",
    ),
    ("password_reset", "en"): DefaultTemplate(
        "Reset your {{ service_name }} password",
        "Hello {{ name }},\n\nSomeone asked to reset the password of your account. If it was "
        "you, set a new one with this link. It works once, until {{ expires_at }}:\n"
        "{{ link_url }}\n\nIf it wasn't you, ignore this email: your password stays the same."
        "\n\n" + _FOOTER_EN,
        "<p>Hello {{ name }},</p><p>Someone asked to reset the password of your account. If it "
        "was you, set a new one with the button below. It works once, until "
        '<strong>{{ expires_at }}</strong>.</p><p><a href="{{ link_url }}" style="display:'
        "inline-block;padding:10px 18px;background:#0F766E;color:#fff;border-radius:8px;"
        "text-decoration:none\">Set a new password</a></p><p>If it wasn't you, ignore this "
        "email: your password stays the same.</p><p>" + _FOOTER_EN + "</p>",
    ),
    ("password_reset", "ar"): DefaultTemplate(
        "إعادة تعيين كلمة المرور في {{ service_name }}",
        "مرحباً {{ name }}،\n\nطلب أحدهم إعادة تعيين كلمة مرور حسابك. إن كنت أنت فعيّن كلمة "
        "جديدة عبر هذا الرابط، وهو صالح لمرة واحدة حتى {{ expires_at }}:\n{{ link_url }}\n\n"
        "إن لم تكن أنت فتجاهل هذه الرسالة وستبقى كلمة مرورك كما هي.\n\n" + _FOOTER_AR,
        "<p>مرحباً {{ name }}،</p><p>طلب أحدهم إعادة تعيين كلمة مرور حسابك. إن كنت أنت فعيّن "
        "كلمة جديدة عبر الزر أدناه، وهو صالح لمرة واحدة حتى <strong>{{ expires_at }}</strong>."
        '</p><p><a href="{{ link_url }}" style="display:inline-block;padding:10px 18px;'
        'background:#0F766E;color:#fff;border-radius:8px;text-decoration:none">تعيين كلمة '
        "مرور جديدة</a></p><p>إن لم تكن أنت فتجاهل هذه الرسالة وستبقى كلمة مرورك كما هي.</p>"
        "<p>" + _FOOTER_AR + "</p>",
    ),
    ("password_invite", "en"): DefaultTemplate(
        "Welcome to {{ service_name }}: set your password",
        "Hello {{ name }},\n\nYour account is ready. Set your password to sign in to the "
        "portal. The link works once, until {{ expires_at }}:\n{{ link_url }}\n\n" + _FOOTER_EN,
        "<p>Hello {{ name }},</p><p>Your account is ready. Set your password to sign in to the "
        'portal. The link works once, until <strong>{{ expires_at }}</strong>.</p><p><a href="'
        '{{ link_url }}" style="display:inline-block;padding:10px 18px;background:#0F766E;'
        'color:#fff;border-radius:8px;text-decoration:none">Set your password</a></p><p>'
        + _FOOTER_EN
        + "</p>",
    ),
    ("password_invite", "ar"): DefaultTemplate(
        "مرحباً بك في {{ service_name }}: عيّن كلمة المرور",
        "مرحباً {{ name }}،\n\nحسابك جاهز. عيّن كلمة المرور لتسجيل الدخول إلى البوابة. الرابط "
        "صالح لمرة واحدة حتى {{ expires_at }}:\n{{ link_url }}\n\n" + _FOOTER_AR,
        "<p>مرحباً {{ name }}،</p><p>حسابك جاهز. عيّن كلمة المرور لتسجيل الدخول إلى البوابة. "
        'الرابط صالح لمرة واحدة حتى <strong>{{ expires_at }}</strong>.</p><p><a href="'
        '{{ link_url }}" style="display:inline-block;padding:10px 18px;background:#0F766E;'
        'color:#fff;border-radius:8px;text-decoration:none">تعيين كلمة المرور</a></p><p>'
        + _FOOTER_AR
        + "</p>",
    ),
}
DEFAULTS: Final = MappingProxyType(_DEFAULTS)


def default_for(key: str, locale: str) -> DefaultTemplate | None:
    return DEFAULTS.get((key, locale)) or DEFAULTS.get((key, "en"))
