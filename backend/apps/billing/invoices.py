"""Invoice documents: a print-ready A4 HTML page in Arabic (RTL) or English (ADR-0012).

The page is self-contained (inline CSS, no scripts, no external requests), so a
browser prints or saves it as PDF exactly as shown. Amounts come from the
invoice's own snapshot fields, never from the live plan or settings.
"""

from decimal import Decimal
from typing import Any, Final

from django.template.loader import render_to_string

from apps.billing import money
from apps.billing.models import Invoice, InvoiceStatus, Payment, PaymentMethod
from apps.core.services import get_setting
from apps.notifications.services import local_time

_TEXT: Final[dict[str, dict[str, str]]] = {
    "en": {
        "title": "Tax invoice",
        "number": "Invoice number",
        "unnumbered": "Not issued yet",
        "date": "Date",
        "bill_to": "Billed to",
        "account": "Account",
        "payment": "Payment",
        "description": "Description",
        "quantity": "Qty",
        "unit_price": "Unit price",
        "amount": "Amount",
        "subtotal": "Subtotal (excl. VAT)",
        "vat": "VAT",
        "vat_number": "VAT number",
        "total": "Total (incl. VAT)",
        "refunded": "Refunded",
        "footer": "Thank you for your subscription.",
    },
    "ar": {
        "title": "فاتورة ضريبية",
        "number": "رقم الفاتورة",
        "unnumbered": "لم تصدر بعد",
        "date": "التاريخ",
        "bill_to": "فاتورة إلى",
        "account": "الحساب",
        "payment": "الدفع",
        "description": "الوصف",
        "quantity": "الكمية",
        "unit_price": "سعر الوحدة",
        "amount": "المبلغ",
        "subtotal": "المجموع (غير شامل الضريبة)",
        "vat": "ضريبة القيمة المضافة",
        "vat_number": "الرقم الضريبي",
        "total": "الإجمالي (شامل الضريبة)",
        "refunded": "المبلغ المسترد",
        "footer": "شكراً لاشتراكك.",
    },
}
_STATUS: Final[dict[str, dict[str, str]]] = {
    "en": {
        InvoiceStatus.PENDING: "Awaiting payment",
        InvoiceStatus.PAID: "Paid",
        InvoiceStatus.VOID: "Void",
        InvoiceStatus.REFUNDED: "Refunded",
    },
    "ar": {
        InvoiceStatus.PENDING: "بانتظار الدفع",
        InvoiceStatus.PAID: "مدفوعة",
        InvoiceStatus.VOID: "ملغاة",
        InvoiceStatus.REFUNDED: "مستردة",
    },
}
_METHODS_AR: Final = {
    PaymentMethod.BANK_TRANSFER: "تحويل بنكي",
    PaymentMethod.CASH: "نقداً",
    PaymentMethod.CARD: "بطاقة",
    PaymentMethod.MADA: "مدى",
    PaymentMethod.APPLE_PAY: "Apple Pay",
    PaymentMethod.STC_PAY: "STC Pay",
    PaymentMethod.OTHER: "أخرى",
}


def _method(value: str, locale: str) -> str:
    if locale == "ar":
        return _METHODS_AR.get(PaymentMethod(value), value)
    return str(PaymentMethod(value).label)


def render(invoice: Invoice, *, locale: str | None = None) -> str:
    """The invoice page; `locale` defaults to the invoice's."""
    lang = locale if locale in ("ar", "en") else invoice.locale
    if lang not in ("ar", "en"):
        lang = "ar"
    currency = invoice.currency

    def amount(value: int) -> str:
        return money.display(value, currency, lang)

    payments: list[Payment] = [
        payment
        for payment in invoice.payments.all()
        if payment.status in ("succeeded", "partially_refunded", "refunded")
    ]
    seller = invoice.seller or {}
    context: dict[str, Any] = {
        "lang": lang,
        "dir": "rtl" if lang == "ar" else "ltr",
        "t": _TEXT[lang],
        "invoice": invoice,
        "accent": str(get_setting("branding.accent_color")),
        "seller_name": seller.get(f"name_{lang}") or seller.get("name_en") or "",
        "issued": local_time(invoice.issued_at or invoice.created_at, invoice.user),
        "status_label": _STATUS[lang].get(invoice.status, invoice.status),
        "lines": [
            {
                "description": line.get(f"description_{lang}") or line.get("description_en"),
                "quantity": line.get("quantity", 1),
                "unit_amount": amount(int(line.get("unit_amount", 0))),
                "amount": amount(int(line.get("amount", 0))),
            }
            for line in invoice.lines
        ],
        "payments": [
            {
                "method": _method(payment.method, lang),
                "amount": amount(payment.amount),
                "reference": payment.reference,
            }
            for payment in payments
        ],
        "subtotal": amount(invoice.subtotal),
        "vat_amount": amount(invoice.vat_amount),
        "vat_percent": format((invoice.vat_rate * Decimal(100)).normalize(), "f"),
        "total": amount(invoice.total),
        "refunded": amount(invoice.refunded_amount),
    }
    return render_to_string("billing/invoice.html", context)


def filename(invoice: Invoice) -> str:
    return f"{invoice.number or f'invoice-{invoice.pk}'}.html"
