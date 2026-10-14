#!/usr/bin/env python3
"""Container hardening gate (G-36, G-39; ADR-0020): `make hardening-test`, in `make ci`.

Renders the production configurations with `docker compose config` (an env file made
from .env.example, never the real one) and checks every service:

- drops every capability (`cap_drop: [ALL]`) and may not gain privileges;
- has a read-only root file system, or is listed below with the reason it can't;
- runs a third-party image pinned by digest (our own `smart-iptv/*` images are built
  from bases pinned by digest, which the Dockerfiles check below covers);
- mounts the Docker socket only if it is one of the read-only API proxies (cAdvisor is
  the documented exception: it needs the container runtime's sockets);
- is never privileged and never on the host's network.

It also checks that every Dockerfile's base image is pinned by digest.
"""

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CONFIGURATIONS = {
    "production": ["docker/compose.yml", "docker/compose.prod.yml"],
    "production with monitoring, CrowdSec and the live ingest": [
        "docker/compose.yml",
        "docker/compose.prod.yml",
        "docker/compose.monitoring.yml",
        "docker/compose.monitoring.prod.yml",
        "docker/compose.crowdsec.yml",
        "docker/compose.mediamtx.yml",
    ],
    # M15 (ADR-0022): the read replica with its exporter, and a standalone edge's host.
    "production with the read replica": [
        "docker/compose.yml",
        "docker/compose.prod.yml",
        "docker/compose.monitoring.yml",
        "docker/compose.monitoring.prod.yml",
        "docker/compose.replica.yml",
        "docker/compose.replica.monitoring.yml",
    ],
    "a standalone edge": ["docker/compose.edge.yml"],
}
#: What a standalone edge's installer writes to its .env (placeholders for rendering).
EDGE_ENV = "EDGE_ID=edge-check\nEDGE_DOMAIN=edge.example.com\nEDGE_CONTROL_URL=https://edges.example.com\n"
PROFILES = ["gpu-nvidia", "restore"]
#: Services that may write to their root file system, and why.
READ_WRITE: dict[str, str] = {}
#: Services that may mount the Docker socket (or the runtime's sockets), and why.
SOCKET: dict[str, str] = {
    "docker-proxy": "Traefik's read-only Docker API proxy",
    "docker-logs-proxy": "Alloy's and CrowdSec's read-only Docker API proxy",
    "cadvisor": "names containers through containerd's socket (documented exception)",
    "node-exporter": "reads the host's file systems through / as nobody (no socket access)",
}
SOCKET_PATHS = ("/var/run/docker.sock", "/run/docker.sock", "/var/run", "/run", "/")
DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")


def render(files: list[str], env_file: Path) -> dict:
    command = ["docker", "compose", "--project-directory", str(REPO), "--env-file", str(env_file)]
    for name in files:
        command += ["-f", str(REPO / name)]
    for profile in PROFILES:
        command += ["--profile", profile]
    command += ["config", "--format", "json"]
    result = subprocess.run(command, capture_output=True, text=True, check=False)  # noqa: S603
    if result.returncode != 0:
        sys.exit(f"docker compose config failed:\n{result.stderr}")
    return json.loads(result.stdout)


def service_problems(name: str, service: dict) -> list[str]:
    problems = []
    if "ALL" not in (service.get("cap_drop") or []):
        problems.append("does not drop every capability")
    if "no-new-privileges:true" not in (service.get("security_opt") or []):
        problems.append("may gain privileges (no no-new-privileges)")
    if not service.get("read_only") and name not in READ_WRITE:
        problems.append("has a writable root file system")
    image = str(service.get("image", ""))
    if not image.startswith("smart-iptv/") and not DIGEST.search(image):
        problems.append(f"image {image} is not pinned by digest")
    if service.get("privileged"):
        problems.append("is privileged")
    if service.get("network_mode") == "host":
        problems.append("uses the host's network")
    for volume in service.get("volumes") or []:
        source = str(volume.get("source", "")) if isinstance(volume, dict) else ""
        if source in SOCKET_PATHS and name not in SOCKET:
            problems.append(f"mounts {source}")
    return problems


def dockerfile_problems() -> list[str]:
    problems = []
    for dockerfile in sorted((REPO / "docker").glob("*.Dockerfile")):
        text = dockerfile.read_text()
        defaults = dict(re.findall(r"^ARG (\w+)=(\S+)", text, re.MULTILINE))
        stages = set(re.findall(r"^FROM \S+ AS (\S+)", text, re.MULTILINE | re.IGNORECASE))
        for base in re.findall(r"^FROM (\S+)", text, re.MULTILINE):
            resolved = re.sub(r"\$\{(\w+)\}", lambda m, d=defaults: d.get(m[1], ""), base)
            if resolved in stages or resolved == "scratch":
                continue
            if not DIGEST.search(resolved):
                problems.append(f"{dockerfile.name}: FROM {base} is not pinned by digest")
    return problems


def main() -> int:
    failures = 0
    with tempfile.TemporaryDirectory() as workdir:
        env_file = Path(workdir) / "hardening.env"
        subprocess.run(  # noqa: S603 (our own script)
            [str(REPO / "scripts" / "secrets.sh"), str(env_file)],
            check=True,
            capture_output=True,
        )
        with env_file.open("a", encoding="utf-8") as handle:
            handle.write(EDGE_ENV)
        for label, files in CONFIGURATIONS.items():
            services = render(files, env_file)["services"]
            bad = {name: service_problems(name, service) for name, service in services.items()}
            bad = {name: problems for name, problems in bad.items() if problems}
            if bad:
                failures += 1
                print(f"  FAIL  {label}:")
                for name, problems in sorted(bad.items()):
                    print(f"          {name}: {'; '.join(problems)}")
            else:
                print(f"  ok    {label}: {len(services)} services hardened")
    problems = dockerfile_problems()
    if problems:
        failures += 1
        print("  FAIL  Dockerfiles:")
        for problem in problems:
            print(f"          {problem}")
    else:
        print("  ok    Dockerfiles: every base image pinned by digest")
    print("Hardening check passed." if not failures else "Hardening check FAILED.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
