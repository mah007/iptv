"""Admin API of the Backups page (ADR-0020):

- GET  /api/v1/admin/backups        status: the backup service, destinations, schedule,
                                     the latest runs and the last restore drill
- GET  /api/v1/admin/backups/runs   the run history, newest first
- POST /api/v1/admin/backups/run    "Back up now", "Run the restore drill", "Test the
                                     destinations"

The settings themselves (schedule, retention, the S3 bucket and its keys) are ordinary
registry settings, group Backups, edited on the Settings page.
"""

from typing import Any, ClassVar

from django.db import transaction
from django.db.models import QuerySet
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import generics, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import HasPermission, Requirements
from apps.audit import services as audit
from apps.audit.services import AuditTarget
from apps.core.errors import ErrorCode, ProblemError
from apps.core.http import acting_user, client_ip
from apps.core.schema import problems
from apps.core.services import get_setting
from apps.ops import backups
from apps.ops.models import DB_KINDS, BackupKind, BackupRun, RunStatus, RunTrigger
from apps.ops.serializers import (
    BackupRunSerializer,
    BackupSummarySerializer,
    RunQuerySerializer,
    RunRequestSerializer,
)

VIEW = "backups.view"
RUN = "backups.run"


class BackupSummaryView(APIView):
    permission_classes = (HasPermission,)
    required_permissions: ClassVar[Requirements] = {"GET": VIEW}

    @extend_schema(
        operation_id="backups_summary",
        summary="Backups: the backup service, destinations, schedule, latest runs and drill",
        responses={200: BackupSummarySerializer, **problems(401, 403)},
    )
    def get(self, request: Request) -> Response:
        return Response(BackupSummarySerializer(backups.summary()).data)


class BackupRunListView(generics.ListAPIView[BackupRun]):
    permission_classes = (HasPermission,)
    required_permissions: ClassVar[Requirements] = {"GET": VIEW}
    serializer_class = BackupRunSerializer
    filter_backends = ()

    def get_queryset(self) -> QuerySet[BackupRun]:
        query = RunQuerySerializer(data=self.request.query_params)
        query.is_valid(raise_exception=True)
        runs: QuerySet[BackupRun] = backups.runs_for_admin()
        if kind := query.validated_data.get("kind"):
            runs = runs.filter(kind=kind)
        if run_status := query.validated_data.get("status"):
            runs = runs.filter(status=run_status)
        return runs.order_by("-created_at", "-id")

    @extend_schema(
        operation_id="backups_runs",
        summary="Backup runs, newest first",
        parameters=[
            OpenApiParameter("kind", str, enum=list(BackupKind.values)),
            OpenApiParameter("status", str, enum=list(RunStatus.values)),
        ],
        responses={200: BackupRunSerializer(many=True), **problems(400, 401, 403)},
    )
    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        return super().get(request, *args, **kwargs)


class BackupRunNowView(APIView):
    permission_classes = (HasPermission,)
    required_permissions: ClassVar[Requirements] = {"POST": RUN}
    step_up_methods = frozenset({"POST"})

    @extend_schema(
        operation_id="backups_run",
        summary="Back up now, run the restore drill, or test the destinations",
        request=RunRequestSerializer,
        responses={202: BackupRunSerializer(many=True), **problems(400, 401, 403, 409)},
    )
    def post(self, request: Request) -> Response:
        body = RunRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        what = body.validated_data["what"]
        if what != "check" and not get_setting("backups.enabled"):
            raise ProblemError(ErrorCode.CONFLICT, "Backups are switched off in the settings.")
        if not backups.agent_state().healthy:
            raise ProblemError(
                ErrorCode.CONFLICT,
                "The backup service is not running on this server, so nothing would run the job.",
            )
        actor = acting_user(request)
        with transaction.atomic():
            if what == "backup":
                if backups.pending_db():
                    raise ProblemError(ErrorCode.CONFLICT, "A backup is already queued or running.")
                runs = backups.queue_nightly(trigger=RunTrigger.MANUAL, actor=actor)
            elif what == "drill":
                if backups.last_success(DB_KINDS) is None:
                    raise ProblemError(
                        ErrorCode.CONFLICT, "There is no backup to restore yet: back up first."
                    )
                runs = [backups.queue(BackupKind.DRILL, trigger=RunTrigger.MANUAL, actor=actor)]
            else:
                runs = [backups.queue(BackupKind.CHECK, trigger=RunTrigger.MANUAL, actor=actor)]
            audit.record(
                f"backup.{what}",
                actor=actor,
                target=AuditTarget("ops.backuprun", str(runs[0].pk)),
                after={"runs": [run.kind for run in runs]},
                ip=client_ip(request),
            )
        return Response(BackupRunSerializer(runs, many=True).data, status=status.HTTP_202_ACCEPTED)
