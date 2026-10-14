# Semgrep's own tests for rules.yml (`make semgrep` runs them first): each `ruleid:` line
# must be found and each `ok:` line must not.
import hmac

import structlog
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt

log = structlog.get_logger(__name__)


def logging(password: str, user_id: int) -> None:
    # ruleid: iptv-log-credential
    log.info("signed in", password=password)
    # ok: iptv-log-credential
    log.info("signed in", user_id=user_id)


def compare(provided: str, expected_signature: str, name: str) -> bool:
    # ruleid: iptv-secret-compared-with-equals
    if provided == expected_signature:
        return True
    # ok: iptv-secret-compared-with-equals
    if name == "MEDIA_TOKEN_KEYS":
        return False
    # ok: iptv-secret-compared-with-equals
    return hmac.compare_digest(provided, expected_signature)


def document(html: str) -> HttpResponse:
    # ruleid: iptv-html-without-csp
    response = HttpResponse(html, content_type="text/html; charset=utf-8")
    return response


def safe_document(html: str) -> HttpResponse:
    # ok: iptv-html-without-csp
    response = HttpResponse(html, content_type="text/html; charset=utf-8")
    response["Content-Security-Policy"] = "default-src 'none'"
    return response


# ruleid: iptv-csrf-exempt
@csrf_exempt
def open_view(request):
    return HttpResponse(b"")


# ruleid: iptv-forbidden-dependency
import tmdbsimple  # noqa: E402

# ruleid: iptv-forbidden-dependency
from qbittorrentapi import Client  # noqa: E402
