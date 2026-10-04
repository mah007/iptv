"""Admin API of the notifications app (SPEC §8.3 Notifications & Templates, §10).

- `notifications`: the delivery log (the outbox), with retry for failed messages;
- `templates`: every event's template in effect per channel and language (the
  admin's version or the code default), editable, revertible, previewable, and
  testable by sending it to oneself.
"""

from typing import Any, ClassVar
from uuid import UUID

from django.db.models import QuerySet
from django.shortcuts import get_object_or_404
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import filters, generics
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import Locale
from apps.accounts.permissions import HasPermission, Requirements
from apps.core.errors import ErrorCode, ProblemError
from apps.core.http import acting_user, client_ip
from apps.core.schema import problems
from apps.notifications import services
from apps.notifications.defaults import COMMON_VARIABLES, EVENTS, SECRET_EVENTS
from apps.notifications.filters import OutboxFilter
from apps.notifications.models import Channel, NotificationOutbox, NotificationTemplate
from apps.notifications.serializers import (
    OutboxDetailSerializer,
    OutboxSerializer,
    PreviewRequestSerializer,
    PreviewSerializer,
    TemplateSerializer,
    TemplateWriteSerializer,
)


class AdminView(APIView):
    permission_classes = (HasPermission,)
    required_permissions: ClassVar[Requirements] = {}


# --- The delivery log --------------------------------------------------------------------


@extend_schema_view(
    get=extend_schema(
        operation_id="notifications_list",
        summary="Messages sent, waiting or failed, newest first",
        responses={200: OutboxSerializer(many=True), **problems(400, 401, 403)},
    )
)
class OutboxListView(generics.ListAPIView[NotificationOutbox]):
    permission_classes = (HasPermission,)
    required_permissions: ClassVar[Requirements] = {"GET": "notifications.view"}
    serializer_class = OutboxSerializer
    filter_backends = (DjangoFilterBackend, filters.SearchFilter)
    filterset_class = OutboxFilter
    search_fields = ("to_address", "subject", "user__username", "user__name")

    def get_queryset(self) -> QuerySet[NotificationOutbox]:
        return NotificationOutbox.objects.select_related("user").order_by("-created_at", "-id")


class OutboxDetailView(AdminView):
    required_permissions: ClassVar[Requirements] = {"GET": "notifications.view"}

    @extend_schema(
        operation_id="notifications_retrieve",
        summary="A message with its template variables",
        responses={200: OutboxDetailSerializer, **problems(401, 403, 404)},
    )
    def get(self, request: Request, pk: UUID) -> Response:
        row = get_object_or_404(NotificationOutbox.objects.select_related("user"), pk=pk)
        return Response(OutboxDetailSerializer(row).data)


class OutboxRetryView(AdminView):
    required_permissions: ClassVar[Requirements] = {"POST": "notifications.manage"}

    @extend_schema(
        operation_id="notifications_retry",
        summary="Queue a failed or skipped message again",
        request=None,
        responses={200: OutboxDetailSerializer, **problems(401, 403, 404, 409)},
    )
    def post(self, request: Request, pk: UUID) -> Response:
        row = services.retry(
            get_object_or_404(NotificationOutbox, pk=pk),
            actor=acting_user(request),
            ip=client_ip(request),
        )
        return Response(OutboxDetailSerializer(row).data)


# --- Templates ----------------------------------------------------------------------------


def template_body(template: services.EffectiveTemplate) -> dict[str, Any]:
    event = EVENTS[template.key]
    return {
        "key": template.key,
        "channel": template.channel,
        "locale": template.locale,
        "description": event.description,
        "variables": [*COMMON_VARIABLES, *event.variables],
        "subject": template.subject,
        "body_text": template.body_text,
        "body_html": template.body_html,
        "enabled": template.enabled,
        "is_default": template.is_default,
        "secret": template.key in SECRET_EVENTS,
        "updated_at": template.row.updated_at if template.row else None,
    }


def _target(key: str, channel: str, locale: str) -> tuple[str, str, str]:
    if key not in EVENTS or channel not in Channel.values or locale not in Locale.values:
        raise ProblemError(ErrorCode.NOT_FOUND, "No such template.")
    return key, channel, locale


class TemplateListView(AdminView):
    required_permissions: ClassVar[Requirements] = {"GET": "notifications.view"}

    @extend_schema(
        operation_id="templates_list",
        summary="Every event's template in effect, per channel and language",
        responses={200: TemplateSerializer(many=True), **problems(401, 403)},
    )
    def get(self, request: Request) -> Response:
        rows = [template_body(template) for template in services.all_templates()]
        return Response(TemplateSerializer(rows, many=True).data)


class TemplateDetailView(AdminView):
    required_permissions: ClassVar[Requirements] = {
        "GET": "notifications.view",
        "PUT": "notifications.manage",
        "DELETE": "notifications.manage",
    }

    @extend_schema(
        operation_id="templates_retrieve",
        summary="One template in effect",
        responses={200: TemplateSerializer, **problems(401, 403, 404)},
    )
    def get(self, request: Request, key: str, channel: str, locale: str) -> Response:
        template = services.effective_template(*_target(key, channel, locale))
        if template is None:
            raise ProblemError(ErrorCode.NOT_FOUND, "No such template.")
        return Response(TemplateSerializer(template_body(template)).data)

    @extend_schema(
        operation_id="templates_update",
        summary="Save the admin's version of a template (checked in the sandbox)",
        request=TemplateWriteSerializer,
        responses={200: TemplateSerializer, **problems(400, 401, 403, 404)},
    )
    def put(self, request: Request, key: str, channel: str, locale: str) -> Response:
        target = _target(key, channel, locale)
        payload = TemplateWriteSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        services.save_template(
            key=target[0],
            channel=target[1],
            locale=target[2],
            values=payload.validated_data,
            actor=acting_user(request),
            ip=client_ip(request),
        )
        template = services.effective_template(*target)
        return Response(TemplateSerializer(template_body(template)).data)  # type: ignore[arg-type]

    @extend_schema(
        operation_id="templates_reset",
        summary="Drop the admin's version: the default applies again",
        responses={200: TemplateSerializer, **problems(401, 403, 404)},
    )
    def delete(self, request: Request, key: str, channel: str, locale: str) -> Response:
        target = _target(key, channel, locale)
        row = NotificationTemplate.objects.filter(
            key=target[0], channel=target[1], locale=target[2]
        ).first()
        if row is not None:
            services.delete_template(row, actor=acting_user(request), ip=client_ip(request))
        template = services.effective_template(*target)
        return Response(TemplateSerializer(template_body(template)).data)  # type: ignore[arg-type]


class TemplatePreviewView(AdminView):
    required_permissions: ClassVar[Requirements] = {"POST": "notifications.view"}

    @extend_schema(
        operation_id="templates_preview",
        summary="Render template text with the event's sample values (nothing is saved)",
        request=PreviewRequestSerializer,
        responses={200: PreviewSerializer, **problems(400, 401, 403)},
    )
    def post(self, request: Request) -> Response:
        payload = PreviewRequestSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        message = services.preview(
            key=data["key"],
            locale=data["locale"],
            subject=data["subject"],
            body_text=data["body_text"],
            body_html=data["body_html"],
            user=acting_user(request),
        )
        return Response(
            PreviewSerializer(
                {"subject": message.subject, "text": message.text, "html": message.html}
            ).data
        )


class TemplateTestView(AdminView):
    required_permissions: ClassVar[Requirements] = {"POST": "notifications.manage"}

    @extend_schema(
        operation_id="templates_test",
        summary="Send the template in effect to me, with sample values",
        request=None,
        responses={202: OutboxSerializer(many=True), **problems(401, 403, 404, 409)},
    )
    def post(self, request: Request, key: str, channel: str, locale: str) -> Response:
        target = _target(key, channel, locale)
        actor = acting_user(request)
        if actor is None:  # HasPermission already requires a signed-in admin
            raise ProblemError(ErrorCode.NOT_AUTHENTICATED)
        rows = services.send_test(target[0], target[2], actor=actor, ip=client_ip(request))
        return Response(OutboxSerializer(rows, many=True).data, status=202)
