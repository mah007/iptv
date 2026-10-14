"""Media edges (SPEC §3, §12; ADR-0022).

An `Edge` is somewhere playback URLs can point:

- the **local** edge: `nginx-stream` on the control host, at `MEDIA_BASE_URL`. It serves
  every rendition from the media volume and is the only edge for live TV. There is
  exactly one, created by the first migration; it can be disabled, never deleted.
- a **standalone** edge on another host (`docker/compose.edge.yml`, `install.sh --edge`)
  with its own TLS. It reads published renditions from the S3 origin (`storage=origin`)
  or a full local mirror (`storage=disk`). It authenticates to the control plane with a
  bearer secret whose SHA-256 is kept here (and mirrored to redis-state for
  stream-auth, which never touches Postgres).
- a **CDN** pull zone (Bunny) in front of one of our edges (`origin_edge`), with
  playback URLs signed by Bunny's token authentication.

Playback start picks one per session (`apps.edges.selection`): enabled, healthy, able to
serve the rendition, under its capacity, weighted. The admin manages them on the Edges
page.
"""

from django.conf import settings
from django.core.validators import MaxValueValidator, RegexValidator
from django.db import models
from django.db.models import Q

from apps.core.models import BaseModel

EDGE_ID_PATTERN = r"^[A-Za-z0-9_.-]{1,64}$"
LOCAL_EDGE_ID = "local"


class EdgeKind(models.TextChoices):
    LOCAL = "local", "This server"
    STANDALONE = "standalone", "Standalone edge"
    CDN = "cdn", "CDN pull zone"


class EdgeStorage(models.TextChoices):
    #: The renditions are on the edge's own disk: the control host, or a full mirror.
    DISK = "disk", "Its own disk"
    #: The edge reads published renditions from the S3 origin through its slice cache.
    ORIGIN = "origin", "The S3 origin"


class Audience(models.TextChoices):
    ALL = "all", "Everyone"
    #: Only the listed customers: for trying a new edge before opening it to everyone.
    CANARY = "canary", "Selected customers"


class Edge(BaseModel):
    #: The name the edge reports in X-Edge-Id and its access log (EDGE_ID).
    edge_id = models.CharField(
        max_length=64, unique=True, validators=[RegexValidator(EDGE_ID_PATTERN)]
    )
    name = models.CharField(max_length=100)
    kind = models.CharField(max_length=16, choices=EdgeKind.choices)
    storage = models.CharField(max_length=8, choices=EdgeStorage.choices, default=EdgeStorage.DISK)
    #: The public origin players reach, e.g. https://media2.example.com. The local edge
    #: keeps it empty: it is MEDIA_BASE_URL.
    base_url = models.CharField(max_length=255, blank=True)
    #: Off: no new sessions, and a standalone edge loses its access to the control plane.
    enabled = models.BooleanField(default=True)
    #: No new sessions; sessions already playing carry on (before maintenance).
    draining = models.BooleanField(default=False)
    #: Its share of new sessions among the eligible edges (0: only as a last resort).
    weight = models.PositiveSmallIntegerField(default=100, validators=[MaxValueValidator(1000)])
    capacity_mbps = models.PositiveIntegerField(default=1000)
    audience = models.CharField(max_length=8, choices=Audience.choices, default=Audience.ALL)
    canary_users = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name="+")
    #: A CDN pull zone's origin: the edge its pulls reach (`/c/<title>/<tail>`).
    origin_edge = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="cdn_edges"
    )
    #: SHA-256 (hex) of a standalone edge's bearer secret; the secret is shown once.
    secret_hash = models.CharField(max_length=64, blank=True)
    secret_set_at = models.DateTimeField(null=True, blank=True)
    #: The last report: a standalone edge's poll, or the local edge's probe.
    last_seen_at = models.DateTimeField(null=True, blank=True)
    report = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ("kind", "name")
        constraints = (
            models.UniqueConstraint(
                fields=("kind",), condition=Q(kind="local"), name="edges_edge_one_local"
            ),
            models.CheckConstraint(
                condition=Q(kind="cdn") | Q(origin_edge__isnull=True),
                name="edges_edge_origin_only_cdn",
            ),
        )

    def __str__(self) -> str:
        return f"edge:{self.edge_id}"
