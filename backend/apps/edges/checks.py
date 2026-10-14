"""Bunny CDN's Test on the Integrations page (M15, ADR-0022).

The token key and the origin secret must both be set. When a CDN zone is configured,
the Test runs its end-to-end probe (apps.edges.probe): a URL signed with Bunny's token
authentication, fetched through the pull zone, which pulls the probe object from its
origin edge's `/c/` location with the origin secret.
"""

from __future__ import annotations

from apps.core.integration import TestCode, TestResult
from apps.core.services import get_secret
from apps.edges import probe
from apps.edges.models import Edge, EdgeKind


def check_bunny() -> TestResult:
    if not get_secret("cdn.bunny_token_key") or not get_secret("cdn.origin_secret"):
        return TestResult.failed(
            TestCode.NOT_CONFIGURED, "Set the pull zone's token key and the origin secret."
        )
    zone = (
        Edge.objects.filter(kind=EdgeKind.CDN, enabled=True).select_related("origin_edge").first()
    )
    if zone is None:
        return TestResult.passed(
            "Both secrets are set. Add the pull zone on the Edges page to test it end to end.",
            zone=False,
        )
    result = probe.run(zone)
    if result.ok:
        return TestResult.passed(f"The pull zone {zone.name} served the probe object.", zone=True)
    failed = next(step for step in result.steps if not step.ok)
    return TestResult.failed(
        TestCode.REJECTED, f"The pull zone's probe failed at {failed.name}: {failed.code}."
    )
