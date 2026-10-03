# compat/: the Xtream contract suite

What IPTV apps receive from the Xtream host (`tv.*`) is a public contract. Smarters, TiviMate, XCIPTV and the rest parse it with PHP-panel types, and a wrong type rarely raises an error: the app shows an empty list or spins forever. This directory pins that contract (SPEC §7.5) and checks it. From M9 on, it gates the Xtream API.

| Path | What it is |
|---|---|
| `schemas/<action>.schema.json` | JSON Schema 2020-12 for every `player_api.php` response. `common.schema.json` holds the shared field types. |
| `fixtures/<action>.json` | Golden responses built from the spec's examples (The Matrix, TMDB 603; Breaking Bad, TMDB 1396). |
| `fixtures/m3u_plus.m3u`, `fixtures/xmltv.xml` | Golden `get.php` and `xmltv.php` documents ([m3u.md](m3u.md), [xmltv.md](xmltv.md)). |
| `fixtures/invalid/*.json` | 146 cases. Each makes one small change to a golden file and states where the check must fail. |
| `validate.py` | The checker for fixtures, invalid cases and, with `--live`, a running server. |
| `fixture_server.py` | A standard-library test double that serves the golden files as an Xtream host. |
| `iptvnator_e2e.py` | The IPTVnator Playwright journey ([iptvnator.md](iptvnator.md)). |
| `clients.md` | The M10 manual client test matrix. |

## Run it

```bash
uvx --with 'jsonschema==4.*' python compat/validate.py        # fixtures and invalid cases
uvx --with 'jsonschema==4.*' python compat/validate.py -v     # also list every problem each invalid case raises
XC_USER=... XC_PASS=... uvx --with 'jsonschema==4.*' python compat/validate.py \
    --live http://tv.localhost:8080 [--play]                    # plus a running server
```

- **Exit status:** 0 when every check passed, 1 when one failed, 2 when the suite could not run (a broken schema, a malformed case file, a missing dependency or missing credentials).
- **Requirements:** Python 3.11+ (tested on 3.12, 3.13 and 3.14) and jsonschema 4.18+ (MIT) with `referencing`. Nothing else.
- **For `make ci`:** pin the exact versions with `uvx --python 3.13 --with 'jsonschema==4.26.0'`.
- **Credentials:** `--live` reads them only from `XC_USER` and `XC_PASS`. All output passes through a redactor that hides those values, the `/movie|series|live|timeshift/<u>/<p>/` path segments, and `username=` and `password=` query values.

A failure names the place, what was found there and the rule it breaks:

```text
  FAIL  get_series_info.json
        $.info.backdrop_path: found string "", expected array [type]
        $.episodes["1"][0].id: found integer 5001, expected string [type]
  FAIL  GET player_api.php action=get_vod_streams
        $[0].rating_5based: found string "4.1", expected number [type]
```

## What is checked

1. **Schema lint.** Every schema keeps these house rules:
   - `$schema` is 2020-12, and `$id` is under `https://smart-iptv.invalid/compat/schemas/`.
   - `null` is never allowed.
   - Every property has a `type`, `$ref`, `const` or `enum`.
   - Every object sets `additionalProperties` on purpose.
   - `required` lists exactly the properties, because PHP panels send every field.
   - Every array defines `items`, and every pattern is anchored with `^` and `$`.
2. **Strict JSON.** Bodies must be UTF-8 without a BOM, with no `NaN` or `Infinity` and no duplicate keys.
3. **Schemas, with strict semantics.** `1.0` is not an integer. Patterns follow ECMA-262, so `$` does not match before a trailing newline (`"1822521600\n"` fails, as it would in a JavaScript client).
4. **Invariants a schema cannot express.** These run once the schema passes:

   | Kind | Rule |
   |---|---|
   | `invariant:real-date` | Dates and date-times are real calendar values (no `2026-02-30`). |
   | `invariant:clock` | `server_info.time_now` is `timestamp_now` in UTC. |
   | `invariant:numbering` | `num` counts from 1 within each response. |
   | `invariant:unique-ids` | No list repeats a `stream_id`, `series_id`, `category_id` or episode `id`. |
   | `invariant:category-ids` | `category_ids` starts with `category_id`. |
   | `invariant:rating-scale` | `rating_5based` is `rating` / 2 to one decimal, and 0 when `rating` is `""`. |
   | `invariant:root-copy` | `get_vod_info` repeats every `info` field at the root with the same value. |
   | `invariant:season-order`, `invariant:seasons` | `episodes` keys are in numeric order. `seasons` has one entry per key, in order, and `episode_count` is the number of listed episodes. |
   | `invariant:episode-order`, `invariant:episode-season` | Within a season, `episode_num` is unique and ascending, and `season` equals the key the episode is listed under. |
   | `invariant:archive` | Live: `tv_archive_duration` is 0 without archive and at least 1 with it. EPG: only finished programmes have `has_archive` 1. |
   | `invariant:epg-times`, `invariant:epg-order` | `stop` is after `start`, `start` and `end` are the timestamps in UTC, and listings are in start order. |
   | `invariant:base64-utf8` | EPG `title` and `description` decode to UTF-8. |
   | `invariant:epg-channel`, `invariant:now-playing` | Listings belong to one channel, and at most one has `now_playing` 1. |
   | `invariant:short-epg` | `get_short_epg` starts at the programme on air. |

5. **Cross-action checks.** Every id in an item's `category_ids` is listed by the matching categories action (`invariant:category-visible`). Movies, episodes and live channels never share an id (`invariant:id-namespace`), because apps key watch history and favourites by stream id alone.
6. **M3U and XMLTV.** These are covered in [m3u.md](m3u.md) and [xmltv.md](xmltv.md). The playlist and the guide list only what the account can see through `player_api.php`.
7. **Live (`--live`).** These run in order:
   1. Login by GET, by POST form body, with `action=get_account_info` and with an unknown action. The username and password must be echoed, and `server_info` must describe the URL the server was reached at.
   2. The three category actions.
   3. Each list unfiltered, filtered to its first category (which must list only that category) and filtered to an unknown category (which must return `[]`).
   4. `get_vod_info` and `get_series_info` for the first items, checked against the lists.
   5. `get_short_epg` with `limit=4` and `get_simple_data_table`, once a stream has an `epg_channel_id` (M12).
   6. The cross-action checks.
   7. `get.php` with `output=ts`, `m3u8` and `mp4`.
   8. `xmltv.php`.
   9. With `--play`, one movie and one episode play URL, each of which must answer 302 to an absolute edge URL (https stays https) without the credentials. This holds a stream slot until the session expires.
   10. Last, because they trip rate limits: a wrong password, an unknown username and a wrong password on an action. All three bodies must be `{"user_info":{"auth":0}}` with HTTP 200 and byte-for-byte identical.

## Invalid cases

Each case in `fixtures/invalid/<golden-file-stem>--<what-breaks>.json` changes one golden file:

```json
{
  "description": "Images are \"\" when missing, never null.",
  "fixture": "get_vod_streams.json",
  "patch": [{ "op": "replace", "path": "/0/stream_icon", "value": null }],
  "expect": { "at": "$[0].stream_icon", "kind": "type" }
}
```

- **The change.** `patch` takes RFC 6902 `add`, `remove` and `replace` operations on JSON fixtures. `replace` is a list of `{old, new}` text edits for the M3U and XMLTV files, and each `old` must occur exactly once.
- **The expectation.** The case passes only when the checker reports a problem at exactly `expect.at` with kind `expect.kind`. A case therefore cannot pass by failing for an unrelated reason, and a change that breaks nothing fails the run (`the changed document passed every check`).
- **Coverage.** The cases cover the type traps in the skill's table: an int `category_id`, string ids, `null` images, `""` or a missing `backdrop_path`, `episodes` shaped as a list or `[]`, string timestamps, and millisecond times. They also cover leaks (`direct_source`, storage keys), formats clients choke on (AVIF and SVG images, MKV containers, YouTube URLs) and every invariant above.

## Use it from tests (M9)

`validate.py` is importable. Problems are values with `where`, `kind` and `message`:

```python
import sys

sys.path.insert(0, str(REPO_ROOT / "compat"))
from validate import check_m3u, check_xmltv, load_suite, parse_json

SUITE = load_suite()  # loads and lints the schemas; raises SuiteError when they are broken


def assert_contract(action: str, body: bytes) -> None:
    payload, problems = parse_json(body)  # strict: duplicate keys, NaN, BOM
    problems = problems or SUITE.check(action, payload)  # schema, then invariants
    assert not problems, "\n".join(map(str, problems))
```

`check_m3u(body, live_ext="ts", origin=..., credentials=...)` and `check_xmltv(body)` return the parsed playlist or guide together with their problems.

## Wire it into `make ci` (M9)

The Makefile, the compose files and the backend's dependencies belong to the backend builders. This is the proposed wiring:

```make
COMPAT_PY := uvx --python 3.13 --with 'jsonschema==4.26.0' python
TV_URL ?= http://tv.localhost:$(shell grep -E '^HTTP_PORT=' .env 2>/dev/null | cut -d= -f2)
# The seeded contract account; read from .env at run time, never echoed.
XC_ENV = XC_USER="$$(grep -E '^COMPAT_XC_USER=' .env | cut -d= -f2-)" \
         XC_PASS="$$(grep -E '^COMPAT_XC_PASS=' .env | cut -d= -f2-)"
IPTVNATOR_IMAGE := 4gray/iptvnator:0.24.0@sha256:c5c33df50735741ba169cae83bf04e2fe356d33cb2ed7612295add2ba0038674

compat: ## Xtream contract suite: schemas, golden fixtures, invalid cases
	$(COMPAT_PY) compat/validate.py

compat-live: ## Contract checks against the running, seeded stack
	@$(XC_ENV) $(COMPAT_PY) compat/validate.py --live $(TV_URL) --play

e2e-iptvnator: ## IPTVnator journey against the running, seeded stack (compat/iptvnator.md)
	docker run --rm -d --name iptvnator-e2e -p 127.0.0.1:4333:80 \
	  --add-host tv.localhost:host-gateway -e CLIENT_URL=http://127.0.0.1:4333 \
	  -e IPTVNATOR_PROXY_ALLOW_PRIVATE_NETWORKS=1 $(IPTVNATOR_IMAGE) > /dev/null
	@for i in $$(seq 1 60); do curl -fsS http://127.0.0.1:4333/api/health > /dev/null && break; sleep 1; done
	@$(XC_ENV) uvx --python 3.13 --with playwright==1.63.0 python compat/iptvnator_e2e.py \
	  --app http://127.0.0.1:4333 --server $(TV_URL) --artifacts dist/iptvnator-e2e; \
	  status=$$?; docker stop iptvnator-e2e > /dev/null; exit $$status
```

- **Where it runs in `ci-steps`.** Add `$(MAKE) compat` after `lint`; it is fast and needs no stack. Add `compat-live` and `e2e-iptvnator` after `test`; they need the stack up and seeded.
- **Credentials.** Never put them on an echoed recipe line. The recipes that use them start with `@`, and the values come from `.env` (which is never committed) at run time. `.env.example` gets empty `COMPAT_XC_USER=` and `COMPAT_XC_PASS=` entries.
- **The seeded contract account.** `make seed` should create a deterministic Xtream account for this job with:
  - sample media that has finished transcoding (a `compat_mp4` exists);
  - at least one movie and one series with a season 0;
  - an Arabic title;
  - `max_connections` of 2 or more, because the IPTVnator journey plays a movie and then an episode, and `--play` holds slots.
- **Backend contract tests.** pytest needs `compat/` inside the web container, read-only (for example `../compat:/compat:ro`), and jsonschema (MIT) as a backend dev dependency. The tests then call `assert_contract()` on Django test-client responses for every action, including after a cache invalidation (SPEC §7.5).

## The fixture server

`fixture_server.py` serves the golden files as an Xtream host for one account and a static catalog. With it you can rehearse `--live` and the IPTVnator journey before the real API exists, or point a real app at the golden shapes.

```bash
python3 compat/fixture_server.py --port 18080 --media /path/to/sample.mp4
XC_USER=mah-7k3p9q XC_PASS=example-password \
    uvx --with 'jsonschema==4.*' python compat/validate.py --live http://127.0.0.1:18080 --play
```

- **Binding and URLs.** It binds 127.0.0.1 unless `--host` says otherwise. `--public-url` sets the URL that clients use, which is the one `server_info` and the play redirects show.
- **Credentials.** `XC_USER` and `XC_PASS` are used when set; otherwise the login fixture's pair.
- **Play URLs.** They redirect (302) to `--media`, which is served with byte ranges. Without `--media` they return 404.
- **What it does not do.** Unknown `vod_id` and `series_id` values get `{}` with 404, which stands in for an open M9 decision. Wrong credentials on `get.php` and `xmltv.php` get an empty 403. There is no rate limiting.
- **Logs.** They redact credentials.

## Decisions this suite encodes beyond SPEC §7.5

M9 must confirm these, or change the schemas and fixtures first. Anything already shipped to clients is a public contract, so ask before breaking it.

1. **`exp_date`** is never `null`, because every account has an end date. PHP panels send `null` for unlimited accounts. The rule for an account that never had a subscription is open.
2. **Time.** `server_info.timezone` is always `"UTC"`, and EPG `start`, `end` and XMLTV times are UTC.
3. **Images.** They are absolute JPEG, PNG or WebP URLs, or `""`. Never AVIF or SVG, because TV apps cannot decode them.
4. **`container_extension`** is always `"mp4"`, because apps always play the compat rendition. **`video`** and **`audio`** are always objects (never `[]`) with only `codec_name`, `width` and `height`, or `codec_name` and `channels`, so ffprobe output never leaks.
5. **Categories.** `category_ids[0]` is `category_id`, and `parent_id` is always 0.
6. **Seasons.** `seasons` lists exactly the seasons that have playable episodes. A season's `id` is an integer: the TMDB season id when known.
7. **Ids.** Movies, episodes and live channels share one `xc_id` sequence.
8. **`youtube_trailer`** is the 11-character video id, never a URL.
9. **EPG.** `get_short_epg` carries `now_playing` and `has_archive` too, because the spec gives both EPG actions one shape. It starts at the programme on air, and `has_archive` is 1 only for finished programmes.
10. **M3U.** The four attributes, the `"<Series> SxxEyy"` episode names and `output=mp4` keeping `.ts` for live are all defined in [m3u.md](m3u.md). The media type is `audio/x-mpegurl`.
11. **XMLTV.** Before M12, `xmltv.php` returns a valid empty `<tv>` ([xmltv.md](xmltv.md)).
12. **Open questions for M9:**
    - What `get_vod_info` and `get_series_info` return for an unknown or hidden id.
    - The status and body of a failed login on `get.php` and `xmltv.php`.
    - Whether `type=m3u` and `output=hls` (an alias some apps send for m3u8) are accepted.

## Add or change an action

Follow the workflow in `.claude/skills/xtream-contract/SKILL.md`:

1. Change the schema first.
2. Update the golden fixture and add invalid cases for each new trap.
3. Run `validate.py` and keep it green.
4. Implement the serializer.
5. Run `--live` and the IPTVnator journey.
