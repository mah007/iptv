"""The compat/ contract suite (compat/README.md, "Use it from tests"), loaded by path.

compat/ sits next to backend/: on the host it is ../compat; in the containers it
must be mounted at /compat (docker/compose.dev.yml: `./compat:/compat:ro`), or
named by XTREAM_COMPAT_DIR. Without it, the tests that need it skip and say so.
"""

import importlib.util
import json
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from types import ModuleType
from typing import Any

from django.conf import settings

MODULE_NAME = "compat_validate"
MISSING = (
    "compat/ is not mounted in this container: add ./compat:/compat:ro to the app "
    "services in docker/compose.dev.yml (or set XTREAM_COMPAT_DIR)"
)


def compat_dir() -> Path | None:
    candidates = (
        os.environ.get("XTREAM_COMPAT_DIR", ""),
        "/compat",
        str(Path(settings.BASE_DIR).parent / "compat"),
    )
    for candidate in candidates:
        if candidate and (Path(candidate) / "validate.py").is_file():
            return Path(candidate)
    return None


@dataclass(frozen=True)
class Contract:
    directory: Path
    module: ModuleType
    suite: Any

    def problems(self, action: str, body: bytes) -> list[str]:
        """Strict JSON, then the action's schema, then its invariants."""
        payload, problems = self.module.parse_json(body)
        found = problems or self.suite.check(action, payload)
        return [str(problem) for problem in found]

    def check(self, action: str, body: bytes) -> Any:
        """The parsed payload; fails the test with every problem the suite found."""
        problems = self.problems(action, body)
        assert not problems, f"{action}:\n" + "\n".join(problems)
        return json.loads(body)

    def fixture(self, name: str) -> Any:
        return json.loads((self.directory / "fixtures" / name).read_bytes())

    def fixture_text(self, name: str) -> str:
        return (self.directory / "fixtures" / name).read_text(encoding="utf-8")

    def check_m3u(
        self, body: bytes, *, live_ext: str, origin: str, credentials: tuple[str, str]
    ) -> tuple[Any, list[str]]:
        playlist, problems = self.module.check_m3u(
            body, live_ext=live_ext, origin=origin, credentials=credentials
        )
        return playlist, [str(problem) for problem in problems]

    def check_xmltv(self, body: bytes) -> list[str]:
        _guide, problems = self.module.check_xmltv(body)
        return [str(problem) for problem in problems]

    def check_catalog(
        self,
        payloads: Mapping[str, Any],
        series_infos: Sequence[Any],
        playlist: Any | None = None,
    ) -> list[str]:
        """Cross-action checks, and the playlist against the catalog when given."""
        catalog = self.module.Catalog(payloads=dict(payloads), series_infos=list(series_infos))
        problems = list(self.module.check_catalog(catalog))
        if playlist is not None:
            problems += self.module.check_playlist_against_catalog(playlist, catalog)
        return [str(problem) for problem in problems]


@cache
def load() -> Contract | None:
    directory = compat_dir()
    if directory is None:
        return None
    spec = importlib.util.spec_from_file_location(MODULE_NAME, directory / "validate.py")
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = module  # dataclasses resolve annotations through it
    spec.loader.exec_module(module)
    return Contract(directory=directory, module=module, suite=module.load_suite())
