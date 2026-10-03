"""Hardware encoder detection (apps/media/hwdetect.py), with a scripted host."""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from apps.media.hwdetect import (
    CommandOutput,
    HostCapabilities,
    best_backend,
    detect,
    live_backends,
    parse_encoders,
    parse_ffmpeg_version,
    parse_nvidia_smi,
    parse_vainfo,
    register,
    run_command,
)
from apps.media.profiles import Backend, Profiles, VideoCodec

ENCODERS = """Encoders:
 V..... = Video
 A..... = Audio
 ------
 V....D libx264              libx264 H.264 / AVC / MPEG-4 AVC / MPEG-4 part 10 (codec h264)
 V....D h264_nvenc           NVIDIA NVENC H.264 encoder (codec h264)
 V..... h264_qsv             H.264 (Intel Quick Sync Video acceleration) (codec h264)
 V....D h264_vaapi           H.264/AVC (VAAPI) (codec h264)
 V....D libx265              libx265 H.265 / HEVC (codec hevc)
 V....D hevc_nvenc           NVIDIA NVENC hevc encoder (codec hevc)
 V..... hevc_qsv             HEVC (Intel Quick Sync Video acceleration) (codec hevc)
 V....D hevc_vaapi           H.265/HEVC (VAAPI) (codec hevc)
 A....D aac                  AAC (Advanced Audio Coding)
"""

VAINFO_INTEL = """libva info: VA-API version 1.20.0
vainfo: Driver version: Intel iHD driver for Intel(R) Gen Graphics - 24.1.0 ()
vainfo: Supported profile and entrypoints
      VAProfileH264Main               :	VAEntrypointVLD
      VAProfileH264High               :	VAEntrypointVLD
      VAProfileH264High               :	VAEntrypointEncSliceLP
      VAProfileHEVCMain               :	VAEntrypointEncSliceLP
      VAProfileHEVCMain10             :	VAEntrypointVLD
      VAProfileHEVCMain10             :	VAEntrypointEncSliceLP
"""

NVIDIA_SMI = (
    "GPU 0: NVIDIA GeForce RTX 3050 6GB Laptop GPU (UUID: GPU-0064a5c2)\n"
    "GPU 1: NVIDIA L4 (UUID: GPU-ffff)\n"
)


@dataclass
class FakeHost:
    """Answers probe commands from a script and records every trial encode."""

    encoders: str | None = ENCODERS
    nvidia: str | None = NVIDIA_SMI
    vainfo: Mapping[str, str] = field(default_factory=dict)
    encodes: Mapping[str, CommandOutput] = field(default_factory=dict)
    calls: list[list[str]] = field(default_factory=list)

    def __call__(self, argv: Sequence[str], timeout_s: float) -> CommandOutput:  # noqa: PLR0911
        args = list(argv)
        self.calls.append(args)
        if args[1:] == ["-hide_banner", "-version"]:
            if self.encoders is None:
                return CommandOutput(127, "", "ffmpeg: not found")
            return CommandOutput(0, "ffmpeg version 7.1.1-Jellyfin Copyright (c) 2000-2025\n", "")
        if args[1:] == ["-hide_banner", "-encoders"]:
            if self.encoders is None:
                return CommandOutput(127, "", "ffmpeg: not found")
            return CommandOutput(0, self.encoders, "")
        if args[0] == "nvidia-smi":
            if self.nvidia is None:
                return CommandOutput(127, "", "nvidia-smi: not found")
            return CommandOutput(0, self.nvidia, "")
        if args[0] == "vainfo":
            text = self.vainfo.get(args[-1])
            if text is None:
                return CommandOutput(1, "", "vaInitialize failed with error code -1")
            return CommandOutput(0, text, "")
        encoder = args[args.index("-c:v") + 1]
        return self.encodes.get(encoder, CommandOutput(0, "", ""))


@pytest.fixture
def host_tree(tmp_path: Path) -> tuple[Path, Path]:
    dri = tmp_path / "dri"
    sysfs = tmp_path / "sys"
    dri.mkdir()
    for node, vendor in (("renderD128", "0x10de"), ("renderD129", "0x8086")):
        (dri / node).touch()
        (sysfs / node / "device").mkdir(parents=True)
        (sysfs / node / "device" / "vendor").write_text(vendor + "\n")
    return dri, sysfs


def _detect(profiles: Profiles, fake: FakeHost, tree: tuple[Path, Path]) -> HostCapabilities:
    dri, sysfs = tree
    return detect(profiles, host="worker-1", dri_dir=str(dri), sysfs_drm=str(sysfs), runner=fake)


def test_detects_working_encoders(profiles: Profiles, host_tree: tuple[Path, Path]) -> None:
    intel = str(host_tree[0] / "renderD129")
    qsv_error = CommandOutput(
        1, "", "Error setting child device handle: -17\nDevice creation failed\n"
    )
    fake = FakeHost(
        vainfo={intel: VAINFO_INTEL},
        encodes={
            "h264_qsv": qsv_error,
            "hevc_qsv": qsv_error,
            "hevc_vaapi": CommandOutput(1, "", "No usable encoding profile found.\n"),
        },
    )
    caps = _detect(profiles, fake, host_tree)

    assert caps.ffmpeg_version == "7.1.1-Jellyfin"
    assert caps.nvidia_gpus == ("NVIDIA GeForce RTX 3050 6GB Laptop GPU", "NVIDIA L4")
    assert [(n.vendor, n.h264_encode, n.hevc_main10_encode) for n in caps.render_nodes] == [
        ("nvidia", False, False),
        ("intel", True, True),
    ]
    assert caps.devices == {Backend.VAAPI: intel, Backend.QSV: intel}
    got = {(c.backend, c.codec): (c.works, c.reason) for c in caps.checks}
    assert got == {
        (Backend.NVENC, VideoCodec.H264): (True, None),
        (Backend.NVENC, VideoCodec.HEVC): (True, None),
        (Backend.QSV, VideoCodec.H264): (False, "encode_failed"),
        (Backend.QSV, VideoCodec.HEVC): (False, "encode_failed"),
        (Backend.VAAPI, VideoCodec.H264): (True, None),
        (Backend.VAAPI, VideoCodec.HEVC): (False, "encode_failed"),
        (Backend.CPU, VideoCodec.H264): (True, None),
        (Backend.CPU, VideoCodec.HEVC): (True, None),
    }
    qsv = next(c for c in caps.checks if c.encoder == "h264_qsv")
    assert qsv.detail == ("Error setting child device handle: -17", "Device creation failed")
    assert caps.backends == (Backend.NVENC, Backend.VAAPI, Backend.CPU)
    assert caps.best is Backend.NVENC
    assert caps.queues == ("transcode.nvenc", "transcode.vaapi", "transcode.cpu")
    assert caps.codecs(Backend.VAAPI) == {VideoCodec.H264}
    assert caps.supports(Backend.NVENC, VideoCodec.HEVC)

    # Trial encodes use the real presets and the detected device.
    qsv_argv = next(call for call in fake.calls if "h264_qsv" in call)
    assert f"qsv=qsv:hw_any,child_device={intel}" in qsv_argv
    hevc_argv = next(call for call in fake.calls if "hevc_nvenc" in call)
    assert hevc_argv[hevc_argv.index("-profile:v") + 1] == "main10"
    assert "format=p010le" in hevc_argv

    document = caps.to_json()
    assert document["best"] == "nvenc"
    assert document["queues"] == ["transcode.nvenc", "transcode.vaapi", "transcode.cpu"]
    assert document["backends"] == {
        "nvenc": ["h264", "hevc"],
        "vaapi": ["h264"],
        "cpu": ["h264", "hevc"],
    }
    assert document["gpus"] == ["NVIDIA GeForce RTX 3050 6GB Laptop GPU", "NVIDIA L4"]
    assert len(document["checks"]) == 8


def test_cpu_only_host(profiles: Profiles, tmp_path: Path) -> None:
    fake = FakeHost(
        encoders=ENCODERS.replace("h264_nvenc", "x").replace("hevc_nvenc", "y"), nvidia=None
    )
    caps = detect(profiles, host="cpu-1", dri_dir=str(tmp_path / "none"), runner=fake)
    reasons = {(c.backend, c.codec): c.reason for c in caps.checks}
    assert reasons[(Backend.NVENC, VideoCodec.H264)] == "not_built"
    assert reasons[(Backend.QSV, VideoCodec.H264)] == "no_device"
    assert reasons[(Backend.VAAPI, VideoCodec.HEVC)] == "no_device"
    assert reasons[(Backend.CPU, VideoCodec.H264)] is None
    assert caps.backends == (Backend.CPU,)
    assert caps.queues == ("transcode.cpu",)
    assert caps.render_nodes == ()


def test_nvenc_needs_a_gpu(profiles: Profiles, tmp_path: Path) -> None:
    caps = detect(profiles, host="h", dri_dir=str(tmp_path), runner=FakeHost(nvidia=""))
    assert {c.reason for c in caps.checks if c.backend is Backend.NVENC} == {"no_device"}


def test_without_ffmpeg_nothing_works(profiles: Profiles, tmp_path: Path) -> None:
    caps = detect(profiles, host="h", dri_dir=str(tmp_path), runner=FakeHost(encoders=None))
    assert caps.ffmpeg_version is None
    assert caps.encoders == frozenset()
    assert {c.reason for c in caps.checks} == {"not_built"}
    assert caps.backends == ()
    assert caps.best is Backend.CPU  # the routing fallback
    assert caps.queues == ()


def test_timeouts_are_reported(profiles: Profiles, tmp_path: Path) -> None:
    fake = FakeHost(nvidia=None, encodes={"libx265": CommandOutput(-9, "", "timed out")})
    caps = detect(profiles, host="h", dri_dir=str(tmp_path), runner=fake)
    check = next(c for c in caps.checks if c.encoder == "libx265")
    assert (check.works, check.reason, check.detail) == (False, "timeout", ("timed out",))


def test_parsers() -> None:
    names = parse_encoders(ENCODERS)
    assert {"libx264", "h264_nvenc", "hevc_vaapi", "aac"} <= names
    assert "=" not in names
    assert parse_vainfo(VAINFO_INTEL) == (
        "Intel iHD driver for Intel(R) Gen Graphics - 24.1.0 ()",
        True,
        True,
    )
    assert parse_vainfo("VAProfileH264High : VAEntrypointVLD\n") == (None, False, False)
    assert parse_nvidia_smi(NVIDIA_SMI) == ("NVIDIA GeForce RTX 3050 6GB Laptop GPU", "NVIDIA L4")
    assert parse_nvidia_smi("No devices were found\n") == ()
    assert parse_ffmpeg_version("ffmpeg version 6.1.1-3ubuntu5 Copyright (c)\n") == "6.1.1-3ubuntu5"
    assert parse_ffmpeg_version("garbage") is None


H264, HEVC = VideoCodec.H264, VideoCodec.HEVC
PREFERENCE = (Backend.NVENC, Backend.QSV, Backend.VAAPI, Backend.CPU)


@pytest.mark.parametrize(
    ("live", "required", "expected"),
    [
        ({Backend.NVENC: {H264, HEVC}, Backend.CPU: {H264, HEVC}}, {H264}, Backend.NVENC),
        ({Backend.VAAPI: {H264}, Backend.CPU: {H264, HEVC}}, {H264, HEVC}, Backend.CPU),
        ({Backend.QSV: {H264, HEVC}, Backend.NVENC: {H264}}, {H264, HEVC}, Backend.QSV),
        ({}, {H264}, Backend.CPU),
        ({Backend.NVENC: {H264}}, set(), Backend.CPU),  # remux: nothing to encode
    ],
)
def test_best_backend(
    live: dict[Backend, set[VideoCodec]], required: set[VideoCodec], expected: Backend
) -> None:
    assert best_backend(live, required, PREFERENCE) is expected


def test_live_backends_merges_worker_documents() -> None:
    documents: list[dict[str, Any]] = [
        {"backends": {"nvenc": ["h264"]}},
        {"backends": {"nvenc": ["hevc"], "cpu": ["h264"], "amf": ["h264"]}},
        {"backends": "bad"},
        {"backends": {"vaapi": ["vp9", "h264"], "qsv": "h264"}},
        {},
    ]
    assert live_backends(documents) == {
        Backend.NVENC: {H264, HEVC},
        Backend.CPU: {H264},
        Backend.VAAPI: {H264},
    }


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, tuple[str, int | None]] = {}

    def set(self, name: str, value: str, ex: int | None = None) -> object:
        self.values[name] = (value, ex)
        return True


def test_register(profiles: Profiles, tmp_path: Path) -> None:
    caps = detect(profiles, host="gpu-7", dri_dir=str(tmp_path), runner=FakeHost())
    client = FakeRedis()
    key = register(client, caps)
    assert key == "worker:caps:gpu-7"
    value, ttl = client.values[key]
    assert ttl == 60
    stored = json.loads(value)
    assert stored["host"] == "gpu-7"
    assert stored["queues"] == ["transcode.nvenc", "transcode.cpu"]
    assert live_backends([stored]) == {Backend.NVENC: {H264, HEVC}, Backend.CPU: {H264, HEVC}}


def test_run_command(tmp_path: Path) -> None:
    assert run_command([str(tmp_path / "missing-binary")], 5).returncode == 127
    slow = run_command([sys.executable, "-c", "import time; time.sleep(5)"], 0.2)
    assert slow.returncode == -9
    done = run_command([sys.executable, "-c", "print('ok')"], 5)
    assert (done.returncode, done.stdout.strip()) == (0, "ok")
