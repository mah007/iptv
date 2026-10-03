"""Hardware encoder detection (SPEC §7.3 "Hardware detection").

On worker start `detect()` lists the encoders ffmpeg was built with
(`ffmpeg -hide_banner -encoders`), the VA-API render nodes (`vainfo` per node) and the
NVIDIA GPUs (`nvidia-smi -L`), then test-encodes a 2 s `testsrc2` clip with each
candidate using the real presets from profiles.yaml (H.264 as for the compat MP4,
HEVC as Main10 for UHD). An encoder counts only when that test encode succeeds: being
compiled in proves nothing (a missing driver or runtime only shows when encoding).

The result says which queues the worker consumes (`transcode.nvenc|qsv|vaapi|cpu`) and
is registered in Redis as `worker:caps:{host}` (TTL 60 s, refreshed by the worker).
`best_backend()` routes a job to the best live backend, falling back to CPU.
"""

from __future__ import annotations

import json
import re
import socket
import subprocess
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, Protocol

from apps.media.ffmpeg import trial_encode_command
from apps.media.profiles import Backend, Profiles, VideoCodec, default_profiles

CAPS_KEY_TEMPLATE: Final = "worker:caps:{host}"
CAPS_TTL_S: Final = 60
PCI_VENDORS: Final = {"0x8086": "intel", "0x1002": "amd", "0x10de": "nvidia"}
_ENCODER_LINE = re.compile(r"^\s*[VAS][A-Z.]{5}\s+(\S+)")
_VERSION_LINE = re.compile(r"^ffmpeg version (\S+)")
_NVIDIA_GPU = re.compile(r"^GPU \d+: (.+?)(?: \(UUID: [^)]*\))?$")
_ENCODE_ENTRYPOINT = re.compile(r"VAEntrypointEncSlice(?:LP)?\b")
_DETAIL_LINES: Final = 5


@dataclass(frozen=True, slots=True)
class CommandOutput:
    returncode: int  # 127 = binary not found, -9 = timed out
    stdout: str
    stderr: str


Runner = Callable[[Sequence[str], float], CommandOutput]


class _RedisSetter(Protocol):
    def set(self, name: str, value: str, ex: int | None = None) -> object: ...


@dataclass(frozen=True, slots=True)
class RenderNode:
    """A DRM render node and what its VA-API driver can encode."""

    path: str
    vendor: str | None  # intel | amd | nvidia | the raw PCI id
    driver: str | None
    h264_encode: bool
    hevc_main10_encode: bool


@dataclass(frozen=True, slots=True)
class EncoderCheck:
    backend: Backend
    codec: VideoCodec
    encoder: str
    device: str | None
    listed: bool  # compiled into ffmpeg
    works: bool  # the test encode succeeded
    reason: str | None  # not_built | no_device | encode_failed | timeout
    detail: tuple[str, ...] = ()  # last stderr lines of a failed test encode
    seconds: float = 0.0


@dataclass(frozen=True, slots=True)
class HostCapabilities:
    host: str
    ffmpeg_version: str | None
    encoders: frozenset[str]
    nvidia_gpus: tuple[str, ...]
    render_nodes: tuple[RenderNode, ...]
    checks: tuple[EncoderCheck, ...]
    devices: Mapping[Backend, str]  # the device each hardware backend uses
    preference: tuple[Backend, ...]
    queue_names: Mapping[Backend, str] = field(default_factory=dict)

    def codecs(self, backend: Backend) -> frozenset[VideoCodec]:
        """Codecs this backend encodes here (HEVC means HEVC Main10)."""
        return frozenset(c.codec for c in self.checks if c.backend is backend and c.works)

    def supports(self, backend: Backend, codec: VideoCodec) -> bool:
        return codec in self.codecs(backend)

    @property
    def backends(self) -> tuple[Backend, ...]:
        """Backends with a working H.264 encoder, best first."""
        return tuple(b for b in self.preference if self.supports(b, VideoCodec.H264))

    @property
    def best(self) -> Backend:
        return self.backends[0] if self.backends else Backend.CPU

    @property
    def queues(self) -> tuple[str, ...]:
        """The Celery queues this worker consumes, best first."""
        return tuple(self.queue_names.get(b, f"transcode.{b.value}") for b in self.backends)

    def to_json(self) -> dict[str, Any]:
        """The `worker:caps:{host}` document."""
        return {
            "host": self.host,
            "ffmpeg": self.ffmpeg_version,
            "checked_at": datetime.now(UTC).isoformat(),
            "best": self.best.value,
            "queues": list(self.queues),
            "backends": {b.value: sorted(c.value for c in self.codecs(b)) for b in self.backends},
            "gpus": list(self.nvidia_gpus),
            "checks": [
                {
                    "backend": c.backend.value,
                    "codec": c.codec.value,
                    "encoder": c.encoder,
                    "works": c.works,
                    "reason": c.reason,
                }
                for c in self.checks
            ],
        }


def detect(  # noqa: PLR0913
    profiles: Profiles | None = None,
    *,
    host: str | None = None,
    ffmpeg: str = "ffmpeg",
    vainfo: str = "vainfo",
    nvidia_smi: str = "nvidia-smi",
    dri_dir: str = "/dev/dri",
    sysfs_drm: str = "/sys/class/drm",
    timeout_s: float = 30.0,
    runner: Runner | None = None,
) -> HostCapabilities:
    """Detect and test every encoder the profiles configure on this host."""
    profiles = profiles or default_profiles()
    run = runner or run_command
    version_output = run([ffmpeg, "-hide_banner", "-version"], timeout_s)
    encoders_output = run([ffmpeg, "-hide_banner", "-encoders"], timeout_s)
    encoders = parse_encoders(encoders_output.stdout) if encoders_output.returncode == 0 else None
    nvidia = run([nvidia_smi, "-L"], timeout_s)
    gpus = parse_nvidia_smi(nvidia.stdout) if nvidia.returncode == 0 else ()
    nodes = tuple(
        _render_node(path, vainfo, sysfs_drm, run, timeout_s)
        for path in sorted(Path(dri_dir).glob("renderD*"))
    )
    devices = _devices(nodes)

    order = [*profiles.backend_preference]
    order += [backend for backend in profiles.backends if backend not in order]
    checks = []
    for backend in order:
        for codec, preset in profiles.backend(backend).encoders.items():
            checks.append(
                _check(
                    profiles,
                    backend,
                    codec,
                    preset.encoder,
                    listed=encoders is not None and preset.encoder in encoders,
                    has_device=_has_device(backend, gpus, devices),
                    device=devices.get(backend),
                    ffmpeg=ffmpeg,
                    run=run,
                    timeout_s=timeout_s,
                )
            )
    return HostCapabilities(
        host=host or socket.gethostname(),
        ffmpeg_version=parse_ffmpeg_version(version_output.stdout),
        encoders=encoders or frozenset(),
        nvidia_gpus=gpus,
        render_nodes=nodes,
        checks=tuple(checks),
        devices=devices,
        preference=tuple(order),
        queue_names={b: p.queue for b, p in profiles.backends.items()},
    )


def best_backend(
    live: Mapping[Backend, Iterable[VideoCodec]],
    required: Iterable[VideoCodec],
    preference: Sequence[Backend],
) -> Backend:
    """The first backend in `preference` that a live worker supports for every required
    codec; CPU when none does. Jobs that encode nothing (remux) always go to CPU."""
    needed = frozenset(required)
    if not needed:
        return Backend.CPU
    for backend in preference:
        if needed <= frozenset(live.get(backend, ())):
            return backend
    return Backend.CPU


def live_backends(documents: Iterable[Mapping[str, Any]]) -> dict[Backend, frozenset[VideoCodec]]:
    """Merge `worker:caps:*` documents into backend -> codecs (unknown values ignored)."""
    merged: dict[Backend, set[VideoCodec]] = {}
    for document in documents:
        backends = document.get("backends")
        if not isinstance(backends, Mapping):
            continue
        for name, codecs in backends.items():
            try:
                backend = Backend(name)
            except ValueError:
                continue
            for codec in codecs if isinstance(codecs, list) else ():
                try:
                    merged.setdefault(backend, set()).add(VideoCodec(codec))
                except ValueError:
                    continue
    return {backend: frozenset(codecs) for backend, codecs in merged.items()}


def register(
    client: _RedisSetter, capabilities: HostCapabilities, *, ttl_s: int = CAPS_TTL_S
) -> str:
    """Store the capabilities under `worker:caps:{host}` with a TTL; returns the key."""
    key = CAPS_KEY_TEMPLATE.format(host=capabilities.host)
    client.set(key, json.dumps(capabilities.to_json(), sort_keys=True), ex=ttl_s)
    return key


# --- parsers ------------------------------------------------------------------------------


def parse_encoders(text: str) -> frozenset[str]:
    """Encoder names from `ffmpeg -encoders` (the lines after the `------` separator)."""
    names = set()
    started = False
    for line in text.splitlines():
        if not started:
            started = line.strip().startswith("---")
            continue
        match = _ENCODER_LINE.match(line)
        if match:
            names.add(match.group(1))
    return frozenset(names)


def parse_ffmpeg_version(text: str) -> str | None:
    for line in text.splitlines():
        match = _VERSION_LINE.match(line.strip())
        if match:
            return match.group(1)
    return None


def parse_nvidia_smi(text: str) -> tuple[str, ...]:
    """GPU names from `nvidia-smi -L`."""
    gpus = []
    for line in text.splitlines():
        match = _NVIDIA_GPU.match(line.strip())
        if match:
            gpus.append(match.group(1).strip())
    return tuple(gpus)


def parse_vainfo(text: str) -> tuple[str | None, bool, bool]:
    """(driver, H.264 encode, HEVC Main10 encode) from `vainfo` output."""
    driver = None
    h264 = hevc10 = False
    for line in text.splitlines():
        if "Driver version:" in line:
            driver = line.split("Driver version:", 1)[1].strip() or None
            continue
        if not _ENCODE_ENTRYPOINT.search(line):
            continue
        profile = line.split(":", 1)[0].strip()
        if profile.startswith("VAProfileH264"):
            h264 = True
        elif profile == "VAProfileHEVCMain10":
            hevc10 = True
    return driver, h264, hevc10


# --- internals ----------------------------------------------------------------------------


def run_command(argv: Sequence[str], timeout_s: float) -> CommandOutput:
    """Run a probe command; missing binaries and timeouts become return codes."""
    try:
        completed = subprocess.run(  # noqa: S603 - argv list, no shell
            list(argv),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        return CommandOutput(127, "", f"{argv[0]}: not found")
    except subprocess.TimeoutExpired:
        return CommandOutput(-9, "", "timed out")
    return CommandOutput(completed.returncode, completed.stdout, completed.stderr)


def _render_node(
    path: Path, vainfo: str, sysfs_drm: str, run: Runner, timeout_s: float
) -> RenderNode:
    vendor_id = _read_text(Path(sysfs_drm) / path.name / "device" / "vendor")
    output = run([vainfo, "--display", "drm", "--device", str(path)], timeout_s)
    driver, h264, hevc10 = (
        parse_vainfo(output.stdout) if output.returncode == 0 else (None, False, False)
    )
    return RenderNode(
        path=str(path),
        vendor=PCI_VENDORS.get(vendor_id, vendor_id) if vendor_id else None,
        driver=driver,
        h264_encode=h264,
        hevc_main10_encode=hevc10,
    )


def _devices(nodes: Sequence[RenderNode]) -> dict[Backend, str]:
    """VA-API: the first node that encodes H.264 (NVIDIA's VA driver only decodes).
    QSV: the first such Intel node (QSV runs on Intel's media driver only)."""
    devices: dict[Backend, str] = {}
    encoding = [node for node in nodes if node.h264_encode and node.vendor != "nvidia"]
    if encoding:
        devices[Backend.VAAPI] = encoding[0].path
    intel = [node for node in encoding if node.vendor == "intel"]
    if intel:
        devices[Backend.QSV] = intel[0].path
    return devices


def _has_device(backend: Backend, gpus: Sequence[str], devices: Mapping[Backend, str]) -> bool:
    if backend is Backend.CPU:
        return True
    if backend is Backend.NVENC:
        return bool(gpus)
    return backend in devices


def _check(  # noqa: PLR0913
    profiles: Profiles,
    backend: Backend,
    codec: VideoCodec,
    encoder: str,
    *,
    listed: bool,
    has_device: bool,
    device: str | None,
    ffmpeg: str,
    run: Runner,
    timeout_s: float,
) -> EncoderCheck:
    def result(works: bool, reason: str | None, **extra: Any) -> EncoderCheck:
        return EncoderCheck(
            backend=backend,
            codec=codec,
            encoder=encoder,
            device=device,
            listed=listed,
            works=works,
            reason=reason,
            **extra,
        )

    if not listed:
        return result(False, "not_built")
    if not has_device:
        return result(False, "no_device")
    command = trial_encode_command(profiles, backend, codec, device=device, ffmpeg=ffmpeg)
    started = time.monotonic()
    output = run(command.argv, timeout_s)
    seconds = round(time.monotonic() - started, 3)
    if output.returncode == 0:
        return result(True, None, seconds=seconds)
    reason = "timeout" if output.returncode == -9 else "encode_failed"
    detail = tuple(line for line in output.stderr.splitlines() if line.strip())[-_DETAIL_LINES:]
    return result(False, reason, detail=detail, seconds=seconds)


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="ascii").strip() or None
    except (OSError, UnicodeDecodeError):
        return None
