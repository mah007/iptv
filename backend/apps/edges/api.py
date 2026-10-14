"""Admin API of the Edges page (ADR-0022), under /api/v1/admin/:

- GET    edges                 the edges, their health and load, and the page's facts
- POST   edges                 add a standalone edge (its secret is in the answer, once)
                               or a CDN zone
- GET    edges/{id}            one edge
- PATCH  edges/{id}            change it (enable, drain, weight, capacity, audience, ...)
- DELETE edges/{id}            remove a standalone edge or a CDN zone (never the local one)
- POST   edges/{id}/secret     a new secret for a standalone edge (shown once)
- POST   edges/{id}/test       the end-to-end probe (apps.edges.probe)

`edges.view` reads, `edges.manage` changes; adding an edge and rotating its secret also
need a recent step-up confirmation (ADR-0021). Every change is audited, secrets never.
"""

from __future__ import annotations

from typing import Any, ClassVar
from urllib.parse import urlsplit

from django.conf import settings
from django.db import transaction
from django.db.models import ProtectedError, QuerySet
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.accounts.permissions import HasPermission, Requirements
from apps.audit import services as audit
from apps.core import registry
from apps.core.errors import ErrorCode, ProblemError
from apps.core.http import acting_user, client_ip
from apps.core.schema import problems
from apps.core.services import get_secret, get_setting, set_setting
from apps.edges import probe, services
from apps.edges.models import Edge, EdgeKind, EdgeStorage
from apps.edges.serializers import (
    EdgeCreatedSerializer,
    EdgeListSerializer,
    EdgeSecretSerializer,
    EdgeSerializer,
    EdgeWriteSerializer,
    ProbeResultSerializer,
)
from apps.playback import tokens
from config.origins import origin

VIEW = "edges.view"
MANAGE = "edges.manage"
#: H1's setting (ADR-0021) for media origins outside <DOMAIN> in the apps' CSP.
CSP_EXTRA_ORIGINS = "security.csp_extra_media_origins"
_AUDITED = (
    "name",
    "base_url",
    "enabled",
    "draining",
    "weight",
    "capacity_mbps",
    "audience",
    "storage",
)


def _queryset() -> QuerySet[Edge]:
    return Edge.objects.select_related("origin_edge").prefetch_related("canary_users")


def _signing_kid() -> str:
    try:
        return tokens.keyring().current.kid
    except tokens.KeySetError:
        return ""


def _snapshot(edge: Edge) -> dict[str, Any]:
    data = {name: getattr(edge, name) for name in _AUDITED}
    data["canary_users"] = sorted(str(user.pk) for user in edge.canary_users.all())
    data["origin_edge"] = str(edge.origin_edge_id) if edge.origin_edge_id else None
    return data


def _get(pk: str) -> Edge:
    edge = _queryset().filter(pk=pk).first()
    if edge is None:
        raise ProblemError(ErrorCode.NOT_FOUND, "There is no such edge.")
    return edge


def _control_url() -> str:
    return origin(settings.PUBLIC_SCHEME, settings.EDGES_HOST, settings.PUBLIC_PORT)


def _outside_domain(base_url: str) -> str:
    """The base URL's origin when its host is outside <DOMAIN> (for the CSP), else ""."""
    host = (urlsplit(base_url).hostname or "").lower()
    domain = str(settings.DOMAIN).lower()
    if not host or host == domain or host.endswith(f".{domain}"):
        return ""
    parts = urlsplit(base_url)
    return f"{parts.scheme}://{parts.netloc}"


def _ensure_csp_origin(base_url: str, actor: User | None, ip: str | None) -> bool:
    """Add an edge outside <DOMAIN> to the apps' extra media origins (H1's CSP setting).
    True when the setting changed: it applies after the next update or restart."""
    extra = _outside_domain(base_url)
    if not extra:
        return False
    try:
        registry.get_definition(CSP_EXTRA_ORIGINS)
    except registry.UnknownSettingError:
        return False
    current = [item.strip() for item in str(get_setting(CSP_EXTRA_ORIGINS)).split(",")]
    if extra in current:
        return False
    set_setting(CSP_EXTRA_ORIGINS, ", ".join([*filter(None, current), extra]), actor=actor, ip=ip)
    return True


def _apply(edge: Edge, data: dict[str, Any]) -> None:
    for name in (*_AUDITED, "edge_id"):
        if name in data:
            setattr(edge, name, data[name])
    if "origin_edge_id" in data:
        origin_id = data["origin_edge_id"]
        if origin_id is None:
            edge.origin_edge = None
        else:
            target = Edge.objects.filter(pk=origin_id).exclude(kind=EdgeKind.CDN).first()
            if target is None or target.pk == edge.pk:
                raise ProblemError(
                    ErrorCode.VALIDATION_ERROR, "A CDN zone's origin must be one of our edges."
                )
            edge.origin_edge = target


def _validate(edge: Edge) -> None:
    if edge.kind == EdgeKind.LOCAL:
        edge.storage = EdgeStorage.DISK
        edge.base_url = ""
        return
    if not edge.base_url:
        raise ProblemError(ErrorCode.VALIDATION_ERROR, "Enter the edge's address.")
    if edge.kind == EdgeKind.CDN and edge.origin_edge_id is None:
        raise ProblemError(ErrorCode.VALIDATION_ERROR, "Choose the edge Bunny pulls from.")
    if edge.kind == EdgeKind.CDN:
        edge.storage = EdgeStorage.DISK


class EdgeListView(APIView):
    permission_classes = (HasPermission,)
    required_permissions: ClassVar[Requirements] = {"GET": VIEW, "POST": MANAGE}
    step_up_methods = frozenset({"POST"})

    @extend_schema(
        operation_id="edges_list",
        summary="Edges, their health and load",
        responses={200: EdgeListSerializer, **problems(401, 403)},
    )
    def get(self, request: Request) -> Response:
        kid = _signing_kid()
        edges = list(_queryset())
        body = {
            "edges": EdgeSerializer(edges, many=True, context={"signing_kid": kid}).data,
            "signing_kid": kid,
            "s3_enabled": bool(get_setting("storage.s3_enabled")),
            "cdn_ready": bool(get_secret("cdn.bunny_token_key")),
            "edges_host": settings.EDGES_HOST,
            "control_url": _control_url(),
        }
        return Response(body)

    @extend_schema(
        operation_id="edges_create",
        summary="Add a standalone edge or a CDN zone",
        request=EdgeWriteSerializer,
        responses={201: EdgeCreatedSerializer, **problems(400, 401, 403, 409)},
    )
    def post(self, request: Request) -> Response:
        body = EdgeWriteSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        data = body.validated_data
        for name in ("edge_id", "name", "kind", "base_url"):
            if not data.get(name):
                raise ProblemError(ErrorCode.VALIDATION_ERROR, f"{name} is required.")
        if Edge.objects.filter(edge_id=data["edge_id"]).exists():
            raise ProblemError(ErrorCode.CONFLICT, "An edge with this id already exists.")
        actor, ip = acting_user(request), client_ip(request)
        edge = Edge(kind=data["kind"], storage=data.get("storage", EdgeStorage.ORIGIN))
        _apply(edge, data)
        _validate(edge)
        secret = ""
        with transaction.atomic():
            if edge.kind == EdgeKind.STANDALONE:
                secret = services.new_secret()
                edge.secret_hash = services.hash_secret(secret)
                edge.secret_set_at = timezone.now()
            edge.save()
            if data.get("canary_user_ids"):
                edge.canary_users.set(data["canary_user_ids"])
            csp_changed = _ensure_csp_origin(edge.base_url, actor, ip)
            audit.record(
                "edge.create",
                actor=actor,
                target=edge,
                after={**_snapshot(edge), "kind": edge.kind, "edge_id": edge.edge_id},
                ip=ip,
            )
        services.changed()
        edge = _get(str(edge.pk))
        context = {
            "secret": secret or None,
            "signing_kid": _signing_kid(),
            "csp_changed": csp_changed,
        }
        return Response(
            EdgeCreatedSerializer(edge, context=context).data, status=status.HTTP_201_CREATED
        )


class EdgeDetailView(APIView):
    permission_classes = (HasPermission,)
    required_permissions: ClassVar[Requirements] = {
        "GET": VIEW,
        "PATCH": MANAGE,
        "DELETE": MANAGE,
    }

    @extend_schema(
        operation_id="edges_get",
        summary="One edge",
        responses={200: EdgeSerializer, **problems(401, 403, 404)},
    )
    def get(self, request: Request, pk: str) -> Response:
        edge = _get(pk)
        return Response(EdgeSerializer(edge, context={"signing_kid": _signing_kid()}).data)

    @extend_schema(
        operation_id="edges_update",
        summary="Change an edge",
        request=EdgeWriteSerializer,
        responses={200: EdgeSerializer, **problems(400, 401, 403, 404)},
    )
    def patch(self, request: Request, pk: str) -> Response:
        edge = _get(pk)
        body = EdgeWriteSerializer(data=request.data, partial=True)
        body.is_valid(raise_exception=True)
        data = dict(body.validated_data)
        for frozen in ("edge_id", "kind"):
            if frozen in data and data[frozen] != getattr(edge, frozen):
                raise ProblemError(ErrorCode.VALIDATION_ERROR, f"{frozen} can't be changed.")
        if edge.kind == EdgeKind.LOCAL:
            for name in ("base_url", "storage", "origin_edge_id"):
                data.pop(name, None)
        actor, ip = acting_user(request), client_ip(request)
        before = _snapshot(edge)
        _apply(edge, data)
        _validate(edge)
        with transaction.atomic():
            edge.save()
            if "canary_user_ids" in data:
                edge.canary_users.set(data["canary_user_ids"])
            csp_changed = "base_url" in data and _ensure_csp_origin(edge.base_url, actor, ip)
            audit.record("edge.update", actor=actor, target=edge, before=before,
                         after=_snapshot(edge), ip=ip)  # fmt: skip
        services.changed()
        edge = _get(pk)
        context = {"signing_kid": _signing_kid(), "csp_changed": csp_changed}
        return Response(EdgeSerializer(edge, context=context).data)

    @extend_schema(
        operation_id="edges_delete",
        summary="Remove an edge",
        responses={204: None, **problems(401, 403, 404, 409)},
    )
    def delete(self, request: Request, pk: str) -> Response:
        edge = _get(pk)
        if edge.kind == EdgeKind.LOCAL:
            raise ProblemError(
                ErrorCode.CONFLICT, "This server's edge can't be removed; disable it instead."
            )
        with transaction.atomic():
            audit.record("edge.delete", actor=acting_user(request), target=edge,
                         before=_snapshot(edge), ip=client_ip(request))  # fmt: skip
            try:
                edge.delete()
            except ProtectedError:
                raise ProblemError(
                    ErrorCode.CONFLICT, "A CDN zone pulls from this edge: remove that first."
                ) from None
        services.changed()
        return Response(status=status.HTTP_204_NO_CONTENT)


class EdgeSecretView(APIView):
    permission_classes = (HasPermission,)
    required_permissions: ClassVar[Requirements] = {"POST": MANAGE}
    step_up_methods = frozenset({"POST"})

    @extend_schema(
        operation_id="edges_rotate_secret",
        summary="A new secret for a standalone edge (the old one stops working at once)",
        request=None,
        responses={200: EdgeSecretSerializer, **problems(401, 403, 404, 409)},
    )
    def post(self, request: Request, pk: str) -> Response:
        edge = _get(pk)
        if edge.kind != EdgeKind.STANDALONE:
            raise ProblemError(ErrorCode.CONFLICT, "Only standalone edges have a secret.")
        with transaction.atomic():
            secret = services.rotate_secret(edge)
            audit.record("edge.rotate_secret", actor=acting_user(request), target=edge,
                         ip=client_ip(request))  # fmt: skip
        return Response({"secret": secret})


class EdgeTestView(APIView):
    permission_classes = (HasPermission,)
    required_permissions: ClassVar[Requirements] = {"POST": MANAGE}

    @extend_schema(
        operation_id="edges_test",
        summary="Test an edge end to end: health, a signed probe URL, its report",
        request=None,
        responses={200: ProbeResultSerializer, **problems(401, 403, 404)},
    )
    def post(self, request: Request, pk: str) -> Response:
        edge = _get(pk)
        result = probe.run(edge)
        audit.record("edge.test", actor=acting_user(request), target=edge,
                     after={"ok": result.ok}, ip=client_ip(request))  # fmt: skip
        return Response(
            {
                "ok": result.ok,
                "steps": [
                    {"name": s.name, "ok": s.ok, "code": s.code, "detail": s.detail}
                    for s in result.steps
                ],
            }
        )
