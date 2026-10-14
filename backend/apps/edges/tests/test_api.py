"""The Edges page's admin API (ADR-0022)."""

from typing import Any

import httpx
import pytest
from django.conf import settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.audit.models import AuditLog
from apps.conftest import AdminFactory, CustomerFactory
from apps.edges import probe, services
from apps.edges.models import Edge, EdgeKind
from apps.playback import tokens

pytestmark = pytest.mark.django_db

ADMIN = {"host": settings.ADMIN_HOST}
LIST = "/api/v1/admin/edges"


def api(user: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(user)
    return client


def create(user: User, **fields: Any) -> Any:
    body: dict[str, Any] = {
        "edge_id": "media2",
        "name": "Media 2",
        "kind": "standalone",
        "storage": "origin",
        "base_url": "https://media2.example.test",
    }
    body.update(fields)
    return api(user).post(LIST, body, format="json", headers=ADMIN)


def test_the_list_needs_edges_view(make_admin: AdminFactory, owner: User) -> None:
    assert api(make_admin("support")).get(LIST, headers=ADMIN).status_code == 403
    response = api(make_admin("viewer")).get(LIST, headers=ADMIN)
    assert response.status_code == 200
    body = response.json()
    assert [edge["edge_id"] for edge in body["edges"]] == ["local"]
    assert body["edges"][0]["effective_url"] == settings.MEDIA_BASE_URL.rstrip("/")
    assert body["edges_host"] == settings.EDGES_HOST
    assert body["s3_enabled"] is False
    assert body["cdn_ready"] is False


def test_adding_a_standalone_edge_shows_its_secret_once(owner: User) -> None:
    response = create(owner)
    assert response.status_code == 201
    body = response.json()
    secret = body["secret"]
    assert services.SECRET_PATTERN.fullmatch(secret)
    assert body["has_secret"] is True
    assert body["status"] == "never_seen"
    edge = Edge.objects.get(edge_id="media2")
    assert edge.secret_hash == services.hash_secret(secret)
    # Never again: not in the list, the detail or the audit log.
    listing = api(owner).get(LIST, headers=ADMIN).content.decode()
    detail = api(owner).get(f"{LIST}/{edge.pk}", headers=ADMIN).content.decode()
    assert secret not in listing
    assert secret not in detail
    entry = AuditLog.objects.get(action="edge.create")
    assert secret not in str(entry.after)


def test_viewers_and_support_cant_change_edges(make_admin: AdminFactory) -> None:
    assert create(make_admin("viewer")).status_code == 403
    assert create(make_admin("support")).status_code == 403


@pytest.mark.parametrize(
    ("fields", "status"),
    [
        ({"edge_id": "bad id"}, 400),
        ({"base_url": "ftp://media2.example.test"}, 400),
        ({"base_url": "https://media2.example.test/path"}, 400),
        ({"kind": "local"}, 400),
        ({"base_url": ""}, 400),
        ({"kind": "cdn"}, 400),
    ],
)
def test_invalid_edges_are_refused(owner: User, fields: dict[str, Any], status: int) -> None:
    assert create(owner, **fields).status_code == status


def test_ids_are_unique(owner: User) -> None:
    assert create(owner).status_code == 201
    assert create(owner).status_code == 409


def test_changing_an_edge_is_audited(owner: User, make_customer: CustomerFactory) -> None:
    edge_id = create(owner).json()["id"]
    customer = make_customer()
    response = api(owner).patch(
        f"{LIST}/{edge_id}",
        {
            "weight": 300,
            "draining": True,
            "audience": "canary",
            "canary_user_ids": [str(customer.pk)],
        },
        format="json",
        headers=ADMIN,
    )
    assert response.status_code == 200
    body = response.json()
    assert (body["weight"], body["draining"], body["audience"]) == (300, True, "canary")
    assert body["canary_user_ids"] == [str(customer.pk)]
    entry = AuditLog.objects.get(action="edge.update")
    assert entry.before is not None
    assert entry.after is not None
    assert entry.before["weight"] == 100
    assert entry.after["weight"] == 300


def test_the_local_edge_keeps_its_address_and_cant_be_removed(owner: User) -> None:
    local = Edge.objects.get(edge_id="local")
    response = api(owner).patch(
        f"{LIST}/{local.pk}",
        {"base_url": "https://elsewhere.example.test", "weight": 50},
        format="json",
        headers=ADMIN,
    )
    assert response.status_code == 200
    local.refresh_from_db()
    assert (local.base_url, local.weight) == ("", 50)
    assert api(owner).delete(f"{LIST}/{local.pk}", headers=ADMIN).status_code == 409


def test_kind_and_id_cant_change(owner: User) -> None:
    edge_id = create(owner).json()["id"]
    for field, value in (("edge_id", "other"), ("kind", "cdn")):
        response = api(owner).patch(
            f"{LIST}/{edge_id}", {field: value}, format="json", headers=ADMIN
        )
        assert response.status_code == 400


def test_a_cdn_zone_needs_an_origin_edge_that_then_cant_go(owner: User) -> None:
    local = Edge.objects.get(edge_id="local")
    response = create(
        owner, edge_id="bunny", kind="cdn", base_url="https://cdn.example.test",
        origin_edge_id=str(local.pk),
    )  # fmt: skip
    assert response.status_code == 201
    assert response.json()["secret"] is None
    zone = Edge.objects.get(edge_id="bunny")
    assert zone.origin_edge_id == local.pk
    origin = create(owner).json()["id"]
    api(owner).patch(f"{LIST}/{zone.pk}", {"origin_edge_id": origin}, format="json", headers=ADMIN)
    assert api(owner).delete(f"{LIST}/{origin}", headers=ADMIN).status_code == 409
    assert api(owner).delete(f"{LIST}/{zone.pk}", headers=ADMIN).status_code == 204
    assert api(owner).delete(f"{LIST}/{origin}", headers=ADMIN).status_code == 204


def test_rotating_a_secret_replaces_the_old_one(owner: User) -> None:
    created = create(owner).json()
    response = api(owner).post(f"{LIST}/{created['id']}/secret", headers=ADMIN)
    assert response.status_code == 200
    new = response.json()["secret"]
    assert new != created["secret"]
    edge = Edge.objects.get(pk=created["id"])
    assert services.secret_matches(new, edge.secret_hash)
    assert not services.secret_matches(created["secret"], edge.secret_hash)
    assert AuditLog.objects.filter(action="edge.rotate_secret").count() == 1
    local = Edge.objects.get(edge_id="local")
    assert api(owner).post(f"{LIST}/{local.pk}/secret", headers=ADMIN).status_code == 409


@pytest.mark.step_up
def test_adding_and_rotating_need_a_recent_confirmation(owner: User) -> None:
    response = create(owner)
    assert (response.status_code, response.json()["code"]) == (403, "STEP_UP_REQUIRED")


def test_an_edge_outside_the_domain_joins_the_csp_media_origins(owner: User) -> None:
    from apps.core import registry  # noqa: PLC0415
    from apps.core.services import get_setting  # noqa: PLC0415

    try:
        registry.get_definition("security.csp_extra_media_origins")
    except registry.UnknownSettingError:
        pytest.skip("M14a's CSP setting isn't in this tree")
    inside = create(owner, base_url=f"https://media2.{settings.DOMAIN}").json()
    assert inside["csp_changed"] is False
    outside = create(owner, edge_id="far", base_url="https://edge.other-cdn.example").json()
    assert outside["csp_changed"] is True
    assert "https://edge.other-cdn.example" in str(get_setting("security.csp_extra_media_origins"))


def test_the_test_action_probes_the_edge_end_to_end(owner: User, media_keys: tokens.KeySet) -> None:
    created = create(owner, storage="disk").json()
    seen: list[str] = []

    def edge(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/healthz":
            return httpx.Response(200, text="ok\n")
        return httpx.Response(200, content=probe.PROBE_BODY)

    probe.use_transport(httpx.MockTransport(edge))
    try:
        response = api(owner).post(f"{LIST}/{created['id']}/test", headers=ADMIN)
    finally:
        probe.use_transport(None)
    assert response.status_code == 200
    steps = {step["name"]: step for step in response.json()["steps"]}
    assert steps["health"]["ok"]
    assert steps["media"]["ok"]
    # It has never polled: the report step says so, and the whole test fails.
    assert steps["report"]["code"] == "never_seen"
    assert response.json()["ok"] is False
    assert seen[0] == "/healthz"
    assert seen[1].endswith("/probe.txt")
    token = seen[1].split("/")[2]
    claims = tokens.verify(media_keys, token, now=tokens.parse(token)[0].exp - 60, tail="probe.txt")
    assert (claims.title, claims.rendition) == ("edgeprobe", "probe")


def test_the_test_action_explains_failures(owner: User) -> None:
    created = create(owner, storage="disk").json()

    def refuse(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/healthz":
            raise httpx.ConnectError("refused")
        return httpx.Response(403, headers={"X-Reason": "edge_unauthorized"})

    probe.use_transport(httpx.MockTransport(refuse))
    try:
        body = api(owner).post(f"{LIST}/{created['id']}/test", headers=ADMIN).json()
    finally:
        probe.use_transport(None)
    steps = {step["name"]: step for step in body["steps"]}
    assert steps["health"]["code"] == "unreachable"
    assert (steps["media"]["code"], steps["media"]["detail"]) == ("http_403", "edge_unauthorized")
    assert body["ok"] is False


def test_list_queries_are_constant(owner: User, django_assert_max_num_queries: Any) -> None:
    for n in range(5):
        create(owner, edge_id=f"edge{n}", base_url=f"https://edge{n}.example.test")
    with django_assert_max_num_queries(12):
        assert api(owner).get(LIST, headers=ADMIN).status_code == 200


def test_kinds_are_what_the_model_says() -> None:
    assert {kind.value for kind in EdgeKind} == {"local", "standalone", "cdn"}
