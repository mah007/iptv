"""A small Meilisearch REST client over httpx (SPEC §7.8).

Only what the search app needs: index settings, documents, search, index swaps and
task waits. The official Python client is a thin wrapper over the same REST API and
would add a dependency (and `requests`) for no gain; httpx is already in the stack.

Every failure (connection, timeout, HTTP error, a failed task) raises `MeiliError`, so
callers fall back to PostgreSQL search with a single `except`.
"""

import time
from collections.abc import Sequence
from functools import cache
from typing import Any, Final, cast

import httpx
from django.conf import settings

TIMEOUT_S: Final = 2.0
TASK_TIMEOUT_S: Final = 120.0


class MeiliError(Exception):
    """Meilisearch is unreachable, refused the request, or a task failed."""


class MeiliIndexMissing(MeiliError):
    """The index does not exist yet (a fresh install): it needs a full build."""


class MeiliClient:
    def __init__(self, url: str, key: str, *, timeout: float = TIMEOUT_S) -> None:
        self._http = httpx.Client(
            base_url=url.rstrip("/"),
            headers={"Authorization": f"Bearer {key}"} if key else {},
            timeout=timeout,
        )

    def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = self._http.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            msg = f"meilisearch unreachable: {type(exc).__name__}"
            raise MeiliError(msg) from None
        if response.status_code == 404 and method == "GET":
            return None
        if response.status_code >= 400:
            try:
                code = response.json().get("code", "")
            except ValueError:
                code = ""
            msg = f"meilisearch {method} {path}: HTTP {response.status_code} {code}"
            if code == "index_not_found":
                raise MeiliIndexMissing(msg)
            raise MeiliError(msg)
        return response.json() if response.content else None

    def healthy(self) -> bool:
        try:
            body = self._call("GET", "/health")
        except MeiliError:
            return False
        return isinstance(body, dict) and body.get("status") == "available"

    def index_exists(self, uid: str) -> bool:
        return self._call("GET", f"/indexes/{uid}") is not None

    def index_uids(self, prefix: str = "") -> list[str]:
        body = self._call("GET", "/indexes", params={"limit": 1000}) or {}
        return [
            str(row["uid"])
            for row in body.get("results", [])
            if str(row.get("uid", "")).startswith(prefix)
        ]

    def create_index(self, uid: str, primary_key: str) -> int:
        task = self._call("POST", "/indexes", json={"uid": uid, "primaryKey": primary_key})
        return int(task["taskUid"])

    def delete_index(self, uid: str) -> int:
        task = self._call("DELETE", f"/indexes/{uid}")
        return int(task["taskUid"])

    def update_settings(self, uid: str, index_settings: dict[str, Any]) -> int:
        task = self._call("PATCH", f"/indexes/{uid}/settings", json=index_settings)
        return int(task["taskUid"])

    def add_documents(self, uid: str, documents: Sequence[dict[str, Any]]) -> int:
        task = self._call(
            "POST", f"/indexes/{uid}/documents", params={"primaryKey": "id"}, json=list(documents)
        )
        return int(task["taskUid"])

    def delete_documents(self, uid: str, ids: Sequence[str]) -> int:
        task = self._call("POST", f"/indexes/{uid}/documents/delete-batch", json=list(ids))
        return int(task["taskUid"])

    def delete_by_filter(self, uid: str, expression: str) -> int:
        task = self._call("POST", f"/indexes/{uid}/documents/delete", json={"filter": expression})
        return int(task["taskUid"])

    def swap(self, first: str, second: str) -> int:
        task = self._call("POST", "/swap-indexes", json=[{"indexes": [first, second]}])
        return int(task["taskUid"])

    def search(self, uid: str, body: dict[str, Any]) -> dict[str, Any]:
        return cast("dict[str, Any]", self._call("POST", f"/indexes/{uid}/search", json=body))

    def wait(self, task_uid: int, timeout: float = TASK_TIMEOUT_S) -> None:
        """Block until the task ends; MeiliError if it failed or took too long."""
        deadline = time.monotonic() + timeout
        delay = 0.05
        while True:
            task = self._call("GET", f"/tasks/{task_uid}")
            status = (task or {}).get("status")
            if status == "succeeded":
                return
            if status in {"failed", "canceled"}:
                error = ((task or {}).get("error") or {}).get("code", "")
                msg = f"meilisearch task {task_uid} {status}: {error}"
                raise MeiliError(msg)
            if time.monotonic() > deadline:
                msg = f"meilisearch task {task_uid} still {status}"
                raise MeiliError(msg)
            time.sleep(delay)
            delay = min(delay * 2, 1.0)


@cache
def client() -> MeiliClient:
    return MeiliClient(settings.MEILI_URL, settings.MEILI_MASTER_KEY)
