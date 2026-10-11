#!/usr/bin/env bash
# Generate legal, synthetic test media for the library scanner, parser and transcoder (SPEC §13).
#
#   scripts/sample_media.sh [TARGET_DIR]          TARGET_DIR defaults to ./media
#
# Every frame and sample comes from FFmpeg's own generators (testsrc2 and smptehdbars video,
# sine audio), and the subtitle text is written here, so the files hold no third-party
# content. Only the names mimic real releases, to exercise the parser; their expected parse
# is in backend/tests/data/filenames.csv (notes "sample_media.sh"). Two libraries are made:
#
#   movies/The.Matrix.1999.1080p.BluRay.x265.mkv
#       HEVC Main 1080p, AC-3 5.1, embedded English subtitles
#   movies/Blade.Runner.2049.2017.2160p.UHD.BluRay.x265.10bit.HDR.mkv
#       HEVC Main 10 2160p HDR10 (BT.2020, SMPTE 2084, mastering display and light level), E-AC-3 5.1
#   movies/Inception (2010)/Inception.2010.1080p.BluRay.DTS.x264.mkv
#       H.264 1080p, DTS 5.1; sidecar Inception.2010.1080p.BluRay.DTS.x264.ar.srt is Arabic
#       in Windows-1256 (cp1256) with CRLF line ends and no BOM, like many real Arabic subtitles
#   movies/وجدة (2012)/وجدة.2012.1080p.WEB-DL.mkv
#       H.264 1080p, AAC stereo in Arabic, Arabic-script title
#   series/Breaking Bad/Season 01/Breaking.Bad.S01E01.720p.mkv
#       H.264 720p, AAC stereo: already direct-play compatible
#   series/Breaking Bad/Season 01/Breaking.Bad.1x02.mkv
#       H.264 480p, MP3 stereo, no colour tags
#   series/Show/Season 02/Show.S02E01E02.mkv
#       H.264 720p, English and Arabic AAC tracks, one chapter per episode
#   movies/The Matrix.mp4
#       H.264 720p, AAC stereo, faststart MP4 (direct play); no year, so the metadata match
#       is ambiguous between the film and its sequels and goes to the review queue
#   live/test-pattern.mp4
#       the live test channel's source (ADR-0017): testsrc2 (a running clock) and a sine
#       tone, H.264 Main 640x360 25 fps with 2 s GOPs, AAC stereo, 60 s; the dev MediaMTX
#       loops it into RTSP. Libraries never scan live/.
#
# Clips run 30 s (the double episode 60 s); the whole set is about 20 MB.
# Idempotent: files that exist are skipped. Each file is written in a hidden work folder and
# renamed into place when complete, so an interrupted run never leaves a partial file behind.
#
# FFmpeg: the ffmpeg on PATH; if there is none (or it lacks an encoder used here), the pinned
# container image below, run with Docker. FFmpeg runs only as a separate process.
# Environment (all optional):
#   SAMPLE_MEDIA_RUNNER=auto|host|docker     choose the FFmpeg runner (default auto)
#   SAMPLE_MEDIA_IMAGE=<image>@sha256:<d>    container image for the docker runner
#   SAMPLE_MEDIA_CONTAINER_PREFIX=<name>     prefix for container names (default sample-media)
set -euo pipefail

# FFmpeg 9.0.2 static build; MIT-licensed Dockerfile (github.com/wader/static-ffmpeg), GPL
# FFmpeg binary. Multi-arch index digest (amd64, arm64), checked 2026-10-03.
readonly DEFAULT_IMAGE='mwader/static-ffmpeg:9.0.2@sha256:7d9bdaaf887f7e6ce6151f67325c344074b5ff1fb75316011c3376503e449a7b'
readonly IMAGE=${SAMPLE_MEDIA_IMAGE:-$DEFAULT_IMAGE}
readonly CONTAINER_PREFIX=${SAMPLE_MEDIA_CONTAINER_PREFIX:-sample-media}
readonly REQUIRED_ENCODERS='libx264 libx265 aac ac3 eac3 dca libmp3lame srt'
readonly REQUIRED_FILTERS='testsrc2 smptehdbars sine join pan overlay format'

# Six tones, one per channel (FL FR FC LFE SL SR), so a wrong channel map is audible.
readonly SINE_51='sine=frequency=440:sample_rate=48000[c0];sine=frequency=554:sample_rate=48000[c1];sine=frequency=659:sample_rate=48000[c2];sine=frequency=60:sample_rate=48000[c3];sine=frequency=330:sample_rate=48000[c4];sine=frequency=392:sample_rate=48000[c5];[c0][c1][c2][c3][c4][c5]join=inputs=6:channel_layout=5.1(side)'
# 1 kHz with a short beep every second (an A/V sync aid), the same on both channels.
readonly SINE_STEREO='sine=frequency=1000:beep_factor=4:sample_rate=48000,pan=stereo|c0=c0|c1=c0'
# 2 s closed GOPs at 23.976 fps, as the transcoder's own outputs use (SPEC §7.3).
readonly X265_GOP='log-level=error:keyint=48:min-keyint=48:scenecut=0'
# BT.709 colour tags for the SDR files (one file is left untagged on purpose).
SDR_TAGS=(-color_primaries bt709 -color_trc bt709 -colorspace bt709)

RUNNER=''
WORK=''
CONTAINER=''
SEQ=0
MADE=0
SKIPPED=0
TOTAL_BYTES=0

die() {
	printf 'sample_media: %s\n' "$*" >&2
	exit 1
}

usage() {
	cat <<'EOF'
Usage: scripts/sample_media.sh [TARGET_DIR]

Generate legal, synthetic test media (FFmpeg testsrc2/smptehdbars video and sine audio) with
release-style names under TARGET_DIR/movies and TARGET_DIR/series. TARGET_DIR defaults to
./media. Existing files are kept. Uses the ffmpeg on PATH, or a pinned FFmpeg container image
through Docker when there is none.

Environment: SAMPLE_MEDIA_RUNNER=auto|host|docker, SAMPLE_MEDIA_IMAGE=<image@digest>,
SAMPLE_MEDIA_CONTAINER_PREFIX=<name>.
EOF
}

cleanup() {
	local status=$?
	if [[ -n $CONTAINER ]]; then
		docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
	fi
	if [[ -n $WORK && -d $WORK ]]; then
		rm -rf -- "$WORK"
	fi
	exit "$status"
}

# Prints what the ffmpeg on PATH lacks for this script; prints nothing when it has it all.
host_ffmpeg_gaps() {
	local encoders filters x265 name gaps=''
	encoders=$(ffmpeg -hide_banner -encoders 2>/dev/null | awk '{ print $2 }') || encoders=''
	filters=$(ffmpeg -hide_banner -filters 2>/dev/null | awk '{ print $2 }') || filters=''
	x265=$(ffmpeg -hide_banner -h encoder=libx265 2>/dev/null) || x265=''
	for name in $REQUIRED_ENCODERS; do
		grep -qx -- "$name" <<<"$encoders" || gaps="$gaps encoder:$name"
	done
	for name in $REQUIRED_FILTERS; do
		grep -qx -- "$name" <<<"$filters" || gaps="$gaps filter:$name"
	done
	grep -q 'yuv420p10le' <<<"$x265" || gaps="$gaps libx265-10bit"
	printf '%s' "${gaps# }"
}

choose_runner() {
	local wanted=${SAMPLE_MEDIA_RUNNER:-auto} gaps
	case $wanted in
	auto | host | docker) ;;
	*) die "SAMPLE_MEDIA_RUNNER must be auto, host or docker, not '$wanted'" ;;
	esac
	if [[ $wanted != docker ]]; then
		if command -v ffmpeg >/dev/null 2>&1; then
			gaps=$(host_ffmpeg_gaps)
			if [[ -z $gaps ]]; then
				RUNNER=host
				return
			fi
			[[ $wanted == auto ]] || die "the ffmpeg on PATH lacks: $gaps"
			printf 'The ffmpeg on PATH lacks %s; using the container image instead.\n' "$gaps"
		elif [[ $wanted == host ]]; then
			die "SAMPLE_MEDIA_RUNNER=host, but there is no ffmpeg on PATH"
		fi
	fi
	command -v docker >/dev/null 2>&1 ||
		die "no usable ffmpeg on PATH and no docker: install FFmpeg (apt install ffmpeg, brew install ffmpeg) or Docker"
	docker info >/dev/null 2>&1 || die "docker is installed, but its daemon is not reachable"
	if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
		printf 'Pulling %s\n' "$IMAGE"
		docker pull --quiet "$IMAGE" >/dev/null || die "could not pull $IMAGE"
	fi
	RUNNER=docker
}

# Runs FFmpeg. Every path is relative to the target folder, which is the working directory
# here and is mounted at /media in the container.
ff() {
	if [[ $RUNNER == host ]]; then
		ffmpeg -hide_banner -nostdin -loglevel error -y "$@"
		return
	fi
	local status=0
	SEQ=$((SEQ + 1))
	CONTAINER="$CONTAINER_PREFIX-$$-$SEQ"
	# Least privilege: no network, the caller's uid and no capabilities but SYS_NICE. That one
	# only makes Docker's default seccomp profile allow set_mempolicy, which libnuma (used by
	# x265) calls from every worker thread; denied, each call prints "set_mempolicy: Operation
	# not permitted". A non-root process gets no effective capability from it.
	# --mount values are CSV: quote the source so commas in the path survive.
	docker run --rm --name "$CONTAINER" --network none --user "$(id -u):$(id -g)" \
		--cap-drop ALL --cap-add SYS_NICE --security-opt no-new-privileges \
		--mount "type=bind,\"source=${PWD//\"/\"\"}\",target=/media" --workdir /media \
		"$IMAGE" -hide_banner -nostdin -loglevel error -y "$@" || status=$?
	CONTAINER=''
	return "$status"
}

human_size() {
	awk -v bytes="$1" 'BEGIN {
		if (bytes < 1048576) printf "%.1f KiB", bytes / 1024
		else printf "%.1f MiB", bytes / 1048576
	}'
}

# account <path> <made|exists> [summary]: prints the result line and adds the size up.
account() {
	local path=$1 state=$2 summary=${3:-} bytes
	bytes=$(wc -c <"$path")
	TOTAL_BYTES=$((TOTAL_BYTES + bytes))
	if [[ $state == made ]]; then
		MADE=$((MADE + 1))
		printf '          %s; %s\n' "$summary" "$(human_size "$bytes")"
	else
		SKIPPED=$((SKIPPED + 1))
		printf '  exists  %s\n' "$path"
	fi
}

# produce <output> <summary> <ffmpeg arguments...>: encodes into the work folder, then renames
# the finished file into place. The first video and audio tracks are flagged default, as in
# typical releases; subtitles are not (lavfi sources carry no dispositions of their own).
produce() {
	local out=$1 summary=$2 tmp muxer
	shift 2
	if [[ -e $out ]]; then
		account "$out" exists
		return
	fi
	printf '  make    %s\n' "$out"
	SEQ=$((SEQ + 1))
	# .mp4 files get the moov atom first (faststart), as direct-play sources need.
	if [[ $out == *.mp4 ]]; then
		tmp="$WORK/$SEQ.mp4"
		muxer=(-movflags +faststart -f mp4)
	else
		tmp="$WORK/$SEQ.mkv"
		muxer=(-default_mode infer_no_subs -f matroska)
	fi
	ff "$@" "${muxer[@]}" "$tmp" || die "ffmpeg failed to make $out"
	mkdir -p -- "$(dirname -- "$out")"
	mv -f -- "$tmp" "$out"
	account "$out" made "$summary"
}

make_matrix() {
	local srt="$WORK/matrix.en.srt"
	cat >"$srt" <<'EOF'
1
00:00:01,000 --> 00:00:04,000
Synthetic test media made by FFmpeg.

2
00:00:05,000 --> 00:00:09,000
The picture is SMPTE colour bars
with a moving test pattern.

3
00:00:10,000 --> 00:00:14,000
<i>The sound is one tone per channel.</i>

4
00:00:20,000 --> 00:00:28,000
No film content is included.
EOF
	produce "movies/The.Matrix.1999.1080p.BluRay.x265.mkv" \
		"HEVC Main 1080p23.976, AC-3 5.1 (eng), SubRip subtitles (eng), 30 s" \
		-i "$srt" \
		-filter_complex "smptehdbars=size=1920x1080:rate=24000/1001[bg];testsrc2=size=480x270:rate=24000/1001[fg];[bg][fg]overlay=x=W-w-96:y=96,format=yuv420p[v];${SINE_51}[a]" \
		-map '[v]' -map '[a]' -map 0:s -t 30 \
		-c:v libx265 -preset ultrafast -crf 30 -x265-params "$X265_GOP:colorprim=bt709:transfer=bt709:colormatrix=bt709" "${SDR_TAGS[@]}" \
		-c:a ac3 -b:a 384k -metadata:s:a:0 language=eng -metadata:s:a:0 'title=English 5.1' \
		-c:s srt -metadata:s:s:0 language=eng -metadata:s:s:0 title=English
}

make_hdr10() {
	# HDR10 signalling: BT.2020 primaries, PQ (SMPTE ST 2084) transfer, BT.2020 non-constant
	# matrix in the container and the VUI, plus mastering display (P3 primaries, D65, 1000 to
	# 0.0001 nits) and content light level (MaxCLL 1000, MaxFALL 400) SEI on every keyframe.
	produce "movies/Blade.Runner.2049.2017.2160p.UHD.BluRay.x265.10bit.HDR.mkv" \
		"HEVC Main 10 2160p23.976 HDR10, E-AC-3 5.1 (eng), 30 s" \
		-filter_complex "smptehdbars=size=3840x2160:rate=24000/1001[bg];testsrc2=size=640x360:rate=24000/1001[fg];[bg][fg]overlay=x=W-w-192:y=192,format=yuv420p10le[v];${SINE_51}[a]" \
		-map '[v]' -map '[a]' -t 30 \
		-c:v libx265 -preset ultrafast -crf 30 -profile:v main10 \
		-x265-params "$X265_GOP:hdr10=1:repeat-headers=1:colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc:range=limited:master-display=G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)L(10000000,1):max-cll=1000,400" \
		-color_primaries bt2020 -color_trc smpte2084 -colorspace bt2020nc -color_range tv \
		-c:a eac3 -b:a 384k -metadata:s:a:0 language=eng
}

make_dts() {
	produce "movies/Inception (2010)/Inception.2010.1080p.BluRay.DTS.x264.mkv" \
		"H.264 High 1080p23.976, DTS 5.1 768 kb/s (eng), 30 s" \
		-filter_complex "smptehdbars=size=1920x1080:rate=24000/1001[bg];testsrc2=size=480x270:rate=24000/1001[fg];[bg][fg]overlay=x=96:y=H-h-96,format=yuv420p[v];${SINE_51}[a]" \
		-map '[v]' -map '[a]' -t 30 \
		-c:v libx264 -preset veryfast -crf 30 -profile:v high -g 48 -keyint_min 48 -sc_threshold 0 "${SDR_TAGS[@]}" \
		-c:a dca -strict experimental -b:a 768k -metadata:s:a:0 language=eng
}

# Arabic subtitles for the DTS file, encoded as many real Arabic .srt files are: Windows-1256
# (cp1256), CRLF line ends, no BOM. The subtitle pipeline must detect and convert them.
make_arabic_srt() {
	local out="movies/Inception (2010)/Inception.2010.1080p.BluRay.DTS.x264.ar.srt"
	local utf8="$WORK/ar.utf8.srt" tmp="$WORK/ar.cp1256.srt"
	if [[ -e $out ]]; then
		account "$out" exists
		return
	fi
	printf '  make    %s\n' "$out"
	awk '{ printf "%s\r\n", $0 }' >"$utf8" <<'EOF'
1
00:00:01,000 --> 00:00:04,500
هذا مقطع اختبار مولَّد بواسطة FFmpeg.

2
00:00:05,000 --> 00:00:09,000
لا يحتوي على أي مشهد من فيلم حقيقي.

3
00:00:10,000 --> 00:00:14,000
الترجمة مرمّزة بترميز Windows-1256،
وتنتهي أسطرها بـ CRLF.

4
00:00:15,000 --> 00:00:19,000
هل تسمع النغمة؟ إنها موجة جيبية.

5
00:00:20,000 --> 00:00:24,000
<i>الصوت: DTS 5.1، والصورة: 1080p.</i>

6
00:00:25,000 --> 00:00:29,000
شُكْرًا لِلمُشاهَدة!
EOF
	iconv -f UTF-8 -t CP1256 "$utf8" >"$tmp" || die "iconv could not encode the Arabic subtitles as CP1256"
	mkdir -p -- "$(dirname -- "$out")"
	mv -f -- "$tmp" "$out"
	account "$out" made "Arabic SubRip, Windows-1256 (cp1256), CRLF, no BOM, 6 cues"
}

make_wadjda() {
	produce "movies/وجدة (2012)/وجدة.2012.1080p.WEB-DL.mkv" \
		"H.264 High 1080p25, AAC-LC stereo (ara), 30 s" \
		-filter_complex "smptehdbars=size=1920x1080:rate=25[bg];testsrc2=size=480x270:rate=25[fg];[bg][fg]overlay=x=96:y=96,format=yuv420p[v];${SINE_STEREO}[a]" \
		-map '[v]' -map '[a]' -t 30 \
		-c:v libx264 -preset veryfast -crf 30 -profile:v high -g 50 -keyint_min 50 -sc_threshold 0 "${SDR_TAGS[@]}" \
		-c:a aac -b:a 128k -metadata:s:a:0 language=ara -metadata 'title=وجدة'
}

make_breaking_bad() {
	produce "series/Breaking Bad/Season 01/Breaking.Bad.S01E01.720p.mkv" \
		"H.264 High 720p23.976, AAC-LC stereo (eng), 30 s" \
		-filter_complex "testsrc2=size=1280x720:rate=24000/1001,format=yuv420p[v];${SINE_STEREO}[a]" \
		-map '[v]' -map '[a]' -t 30 \
		-c:v libx264 -preset veryfast -crf 34 -profile:v high -g 48 -keyint_min 48 -sc_threshold 0 "${SDR_TAGS[@]}" \
		-c:a aac -b:a 128k -metadata:s:a:0 language=eng
	produce "series/Breaking Bad/Season 01/Breaking.Bad.1x02.mkv" \
		"H.264 Main 480p29.97, MP3 stereo 44.1 kHz (eng), no colour tags, 30 s" \
		-filter_complex "testsrc2=size=854x480:rate=30000/1001,format=yuv420p[v];sine=frequency=880:beep_factor=2:sample_rate=44100,pan=stereo|c0=c0|c1=c0[a]" \
		-map '[v]' -map '[a]' -t 30 \
		-c:v libx264 -preset veryfast -crf 32 -profile:v main -g 60 -keyint_min 60 -sc_threshold 0 \
		-c:a libmp3lame -b:a 128k -metadata:s:a:0 language=eng
}

# Deliberately ambiguous: no year, and "The Matrix" also names its sequels, so the metadata
# match goes to the review queue. A faststart H.264/AAC MP4 that plays as is, so the title
# becomes ready once the review is resolved.
make_ambiguous() {
	produce "movies/The Matrix.mp4" \
		"H.264 High 720p23.976, AAC-LC stereo (eng), faststart MP4, 30 s" \
		-filter_complex "testsrc2=size=1280x720:rate=24000/1001,format=yuv420p[v];${SINE_STEREO}[a]" \
		-map '[v]' -map '[a]' -t 30 \
		-c:v libx264 -preset veryfast -crf 34 -profile:v high -level:v 4.0 -g 48 -keyint_min 48 -sc_threshold 0 "${SDR_TAGS[@]}" \
		-c:a aac -b:a 128k -metadata:s:a:0 language=eng
}

make_live_source() {
	produce "live/test-pattern.mp4" \
		"H.264 Main 640x360 25 fps, 2 s GOP, AAC stereo, 60 s (the live test channel)" \
		-filter_complex "testsrc2=size=640x360:rate=25,format=yuv420p[v];${SINE_STEREO}[a]" \
		-map '[v]' -map '[a]' -t 60 \
		-c:v libx264 -preset veryfast -profile:v main -b:v 600k -maxrate 700k -bufsize 1200k \
		-g 50 -keyint_min 50 -sc_threshold 0 "${SDR_TAGS[@]}" \
		-c:a aac -b:a 64k -ac 2
}

make_double_episode() {
	local chapters="$WORK/show.ffmetadata"
	cat >"$chapters" <<'EOF'
;FFMETADATA1
title=Show S02E01E02

[CHAPTER]
TIMEBASE=1/1000
START=0
END=30000
title=Episode 1

[CHAPTER]
TIMEBASE=1/1000
START=30000
END=60000
title=Episode 2
EOF
	produce "series/Show/Season 02/Show.S02E01E02.mkv" \
		"H.264 High 720p25, AAC-LC stereo (eng, default) + AAC-LC stereo (ara), 2 chapters, 60 s" \
		-i "$chapters" \
		-filter_complex "smptehdbars=size=1280x720:rate=25[bg];testsrc2=size=320x180:rate=25[fg];[bg][fg]overlay=x=W-w-64:y=H-h-64,format=yuv420p[v];${SINE_STEREO}[en];sine=frequency=750:beep_factor=4:sample_rate=48000,pan=stereo|c0=c0|c1=c0[ar]" \
		-map '[v]' -map '[en]' -map '[ar]' -map_metadata 0 -map_chapters 0 -t 60 \
		-c:v libx264 -preset veryfast -crf 30 -profile:v high -g 50 -keyint_min 50 -sc_threshold 0 "${SDR_TAGS[@]}" \
		-c:a aac -b:a 96k \
		-metadata:s:a:0 language=eng -metadata:s:a:0 title=English \
		-metadata:s:a:1 language=ara -metadata:s:a:1 'title=العربية' \
		-disposition:a:0 default -disposition:a:1 0
}

main() {
	case ${1:-} in
	-h | --help)
		usage
		return
		;;
	-*) die "unknown option '$1' (see --help)" ;;
	esac
	[[ $# -le 1 ]] || die "expected at most one TARGET_DIR (see --help)"
	command -v iconv >/dev/null 2>&1 || die "iconv is required to write the Windows-1256 subtitle"

	local target=${1:-./media} runner_label
	# Settle on FFmpeg before creating anything, so a bad setup leaves no folders behind.
	choose_runner
	mkdir -p -- "$target/movies" "$target/series" "$target/live" || die "cannot create $target"
	cd -- "$target"
	if [[ $RUNNER == host ]]; then
		runner_label="FFmpeg $(ffmpeg -hide_banner -version | sed -n '1s/^ffmpeg version \([^ ]*\).*/\1/p') on PATH"
	else
		runner_label="FFmpeg in $IMAGE"
	fi

	trap cleanup EXIT
	trap 'exit 130' INT
	trap 'exit 143' TERM
	WORK=$(mktemp -d .sample-media.XXXXXX)

	printf 'Sample media in %s (%s)\n' "$(pwd -P)" "$runner_label"
	make_matrix
	make_hdr10
	make_dts
	make_arabic_srt
	make_wadjda
	make_breaking_bad
	make_double_episode
	make_ambiguous
	make_live_source
	printf 'Done: %d made, %d already there; %s in movies/ and series/.\n' \
		"$MADE" "$SKIPPED" "$(human_size "$TOTAL_BYTES")"
}

main "$@"
