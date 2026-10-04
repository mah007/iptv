# Compat MP4 transcode benchmark

- **Measured:** 2026-10-04
- **What:** the production compat MP4 job (SPEC §7.3: H.264 High ≤ L4.1, ≤ 1080p, capped quality, AAC stereo plus the 5.1 track, `+faststart`), exactly as the transcoder runs it: the P3 planner, the `streaming/ffmpeg/profiles.yaml` presets, jellyfin-ffmpeg 8.1.3, and output verification.
- **Tool:** `manage.py bench_transcode <file under /media>... --backends cpu,nvenc,qsv,vaapi`, run in the `transcoder` container. Wall time includes ffmpeg start-up and the verification probe.

## Dev machine
- **CPU:** Intel Core i5-13450HX (10 cores, 16 threads).
- **NVENC:** NVIDIA RTX 3050 6 GB Laptop, driver 595.91.07 (`GPU=nvidia`).
- **QSV and VA-API:** Intel Raptor Lake UHD on `/dev/dri`, through the iHD driver and the oneVPL runtime bundled in jellyfin-ffmpeg (`GPU=intel`).
- **Detection:** started with `make up GPU=nvidia,intel`, hwdetect found working H.264 and HEVC encoders on all four backends. QSV works in the container, because jellyfin-ffmpeg ships its own oneVPL runtime (the host has none).

### Hard source: what real films cost
The clip is 60 s of 1080p23.976 HEVC at about 50 Mb/s, made with heavy temporal noise (`testsrc2` + `noise=alls=22:allf=t+u`), plus AC-3 5.1 audio. Noise defeats motion prediction, so this is a worst case, harder than most film content. To regenerate it, see [Reproduce](#reproduce).

| Backend | Encoder | Wall | Speed (media s / s) | Avg fps | Output |
| --- | --- | ---: | ---: | ---: | --- |
| cpu | libx264 `-preset slow` | 58.0 s | 1.03× | 25 | 1080p, 8.4 Mb/s (at the 7.8 M cap) |
| nvenc | h264_nvenc `p5 hq` | 7.6 s | 7.88× | 189 | 1080p, 8.4 Mb/s |
| qsv | h264_qsv | 14.6 s | 4.12× | 99 | 1080p, 5.7 Mb/s |
| vaapi | h264_vaapi | 8.5 s | 7.09× | 170 | 1080p, 5.7 Mb/s |

### The sample library (`make sample-media`)
The sample files are 30 s synthetic clips (`testsrc2`), which are easy to encode, so the totals mostly measure start-up, decoding and filters.

| File | Source | cpu | nvenc | qsv | vaapi |
| --- | --- | ---: | ---: | ---: | ---: |
| The.Matrix.1999.1080p.BluRay.x265.mkv | HEVC 1080p SDR | 3.7 s (8.2×) | 3.4 s (8.9×) | 3.0 s (10.0×) | 2.4 s (12.4×) |
| Blade.Runner.2049…2160p…HDR.mkv | HEVC 2160p 10-bit HDR10 → 1080p, tone-mapped | 11.9 s (2.5×) | 10.9 s (2.8×) | 13.0 s (2.3×) | 12.1 s (2.5×) |
| Inception.2010.1080p…DTS.x264.mkv | H.264 1080p, DTS 5.1 (**remux**: video copied, audio to AAC) | 0.9 s (34×) | 0.8 s | 0.9 s | 0.8 s |

**Notes:**
- **The HDR source** is bound by software tone mapping (`zscale` + `tonemap` in the filter chain). That cost is the same on every backend, so a GPU barely helps. Hardware tone mapping (jellyfin-ffmpeg's `tonemap_opencl`/`tonemap_vaapi`) is a later optimisation.
- **A remux** (H.264 in MKV, or non-AAC audio) never needs a GPU. The planner always routes it to `transcode.cpu`, and it runs at tens of times real time.
- **As live jobs** in the dev stack, the remuxes finished in under 1 s each, and the HEVC Matrix took 4 s on CPU (the `media.job_done` logs).

## Server (tv.mah007.net): CPU only
- **The server has no GPU.** Its transcoder detects only libx264, subscribes to `transcode.cpu` alone, and runs one job at a time (`TRANSCODER_CONCURRENCY=1`). `-preset slow` already uses every core.
- **Expected speed:** on the hard source above, x264 managed about 1× real time with 16 threads here. A server with fewer cores will be proportionally slower, so a 2 h film may take 2–4 h of encoding. Most sources are cheaper than this clip, and remuxes take seconds.
- **Ingest time:** titles are `ready` once their compat MP4 exists. Direct-play sources (faststart H.264/AAC MP4) are ready immediately, with no job.
- **To measure there** (after deploying): `docker compose ... exec transcoder python manage.py bench_transcode <file under /media>`. Then add the table here.

## Reproduce
```sh
make up GPU=nvidia,intel            # or no GPU= for CPU only
mkdir -p media/bench
docker compose --project-directory . -f docker/compose.yml -f docker/compose.dev.yml run --rm --no-deps -T \
  -v "$PWD/media/bench:/out" web ffmpeg -hide_banner -nostdin -v error -y \
  -f lavfi -i "testsrc2=size=1920x1080:rate=24000/1001:duration=60,noise=alls=22:allf=t+u" \
  -f lavfi -i "sine=frequency=300:duration=60" \
  -filter_complex "[1:a]pan=5.1|c0=c0|c1=c0|c2=c0|c3=c0|c4=c0|c5=c0[a]" -map 0:v -map "[a]" \
  -c:v libx265 -preset ultrafast -crf 20 -x265-params log-level=error -c:a ac3 -b:a 384k \
  /out/noisy.1080p.hevc.mkv
docker compose --project-directory . -f docker/compose.yml -f docker/compose.dev.yml \
  -f docker/compose.gpu-nvidia.yml -f docker/compose.gpu-intel.yml exec -T transcoder \
  python manage.py bench_transcode bench/noisy.1080p.hevc.mkv --backends cpu,nvenc,qsv,vaapi
```
