# get.php: the M3U playlist (`m3u_plus`)

Apps that take a playlist URL instead of an Xtream login use this playlist, for example SmartOne, UHF in M3U mode and TiviMate's M3U playlist. Apps that take an Xtream login read the same catalog through `player_api.php`. The two must agree.

- **Golden file:** `fixtures/m3u_plus.m3u`.
- **Checker:** `check_m3u()` in `validate.py`, run on the golden file and, with `--live`, on `output=ts`, `m3u8` and `mp4`.
- **Labels in this page:**
  - **[checked]** means `validate.py` fails a playlist that breaks the rule.
  - **[recommended]** marks behaviour the checker does not enforce yet.

## Request

```text
GET /get.php?username=<u>&password=<p>&type=m3u_plus&output=ts|m3u8|mp4
```

- **Credentials** go in the query, like the rest of the Xtream host. Failed authentication must look identical for an unknown user and a wrong password, and is rate limited like `player_api.php`. The exact status is an open M9 decision; the fixture server answers 403 with an empty body.
- **`output`** sets the extension of **live** URLs only:

  | `output` | Live URLs (M12) | Movie and episode URLs |
  |---|---|---|
  | `ts` | `.ts` | `.mp4` |
  | `m3u8` | `.m3u8` | `.mp4` |
  | `mp4` | `.ts` (live is never MP4) | `.mp4` |

  Movies and episodes always end in their `container_extension`, which is always `mp4`: the compatible rendition. **[checked]**
- **Settings.** Live channels arrive in M12. VOD and episodes are included while the "include VOD in M3U" setting is on, which is the default. Before M12 the playlist holds only VOD and episodes.

## Response

- **Status and media type.** `200` with `Content-Type: audio/x-mpegurl`; a `charset=utf-8` parameter is fine. **[checked]** `Content-Disposition: attachment; filename="playlist.m3u"` is **[recommended]**.
- **Encoding.**
  - The body is UTF-8 without a BOM, with LF line endings only (no CR). **[checked]**
  - It ends with a newline. **[recommended]**
  - Compress with gzip when `Accept-Encoding` allows it. **[recommended]** Playlists are large; the checker decodes gzip.
- **Contents.**
  - Only what the account can see: every movie id is in `get_vod_streams`, every live id in `get_live_streams`, and every episode id in `get_series_info`. **[checked]** The live run checks episodes only when it fetched the info of every listed series.
  - No duplicate URLs. **[checked]**
- **Order.** **[recommended]** Live channels first, then movies, then episodes. Within each group, follow the `player_api.php` order (category `sort`, then the list order). Episodes go by series, then season (`S00` first), then episode number.

## Line by line

The first line is the header. **[checked]**

```text
#EXTM3U url-tvg="https://tv.example.com/xmltv.php?username=<u>&password=<p>"
```

- **`url-tvg`** is required:
  - an absolute `http(s)` URL with the path `/xmltv.php`;
  - on the same origin as `server_info` (scheme, host and port);
  - with the same credentials the playlist was fetched with, form-encoded in the query.
- **`x-tvg-url`** is optional; if present, it equals `url-tvg`.
- **Attributes** are `name="value"` pairs with lower-case names.

After the header come entries, and nothing else. Each entry is exactly two lines. **[checked]**

```text
#EXTINF:-1 tvg-id="<id>" tvg-name="<name>" tvg-logo="<image URL or empty>" group-title="<category>",<name>
<play URL>
```

- **Duration.** It is always `-1`.
- **Attributes.** Exactly `tvg-id`, `tvg-name`, `tvg-logo` and `group-title`, each written once.
- **What else is allowed.** No blank lines, no other directives (`#EXTVLCOPT`, `#EXTGRP`, `#KODIPROP` and so on), and no URL line without its `#EXTINF`.
- **Display name.** The text after the comma that follows the attributes. It is non-empty and trimmed, and it equals `tvg-name`. It may contain commas.
- **Escaping.** Values and names never contain `"`, CR or LF. The writer replaces `"` with `'` and each line break with a space. **[checked]** The writer's exact replacement is **[recommended]**.

| | Live channel (M12) | Movie | Episode |
|---|---|---|---|
| `tvg-id` | The channel's `epg_channel_id`, or `""` without a guide. No whitespace. **[checked]** | `""` **[checked]** | `""` **[checked]** |
| `tvg-name` and display name | Channel name | `get_vod_streams` `name` | `<Series name> SxxEyy` with two-digit (or wider) numbers, for example `Breaking Bad S01E02`; specials are `S00`. **[checked]** |
| `tvg-logo` | Channel logo | Poster (`stream_icon`) | Series poster (`cover`) |
| `group-title` | The channel's category name | The primary category's name (`category_id`) | The series' primary category name |
| URL | `/live/<u>/<p>/<xc_id>.<ts\|m3u8>` | `/movie/<u>/<p>/<xc_id>.mp4` | `/series/<u>/<p>/<episode id>.mp4` |

- **`tvg-logo`** is `""` or an absolute JPEG, PNG or WebP URL. **[checked]**
- **`group-title`** is never empty, and it uses the account's locale (ar or en), like `category_name`. **[checked]** (that it is non-empty)
- **URLs** are absolute and on the `server_info` origin, with no query or fragment. **[checked]**
  - The credentials are the ones the playlist was fetched with, percent-encoded as path segments (RFC 3986). The checker decodes them before it compares.
  - The id is the `xc_id`, the same number `player_api.php` returns as `stream_id` or episode `id`.
  - Requesting the URL follows the play rules in SPEC §7.5: entitlement and a slot, then a 302 to the signed edge URL.

## Golden sample

`fixtures/m3u_plus.m3u` holds two live channels (one Arabic, one without a logo), two movies (The Matrix and an Arabic documentary) and three Breaking Bad episodes, including the special `S00E01`. The excerpt below shows its header and four of its seven entries:

```text
#EXTM3U url-tvg="https://tv.example.com/xmltv.php?username=mah-7k3p9q&password=example-password"
#EXTINF:-1 tvg-id="showcase.example" tvg-name="Showcase 24/7" tvg-logo="https://media.example.com/images/channel-3001/logo/w185.eccbc87e.webp" group-title="Showcase",Showcase 24/7
https://tv.example.com/live/mah-7k3p9q/example-password/3001.ts
#EXTINF:-1 tvg-id="family-cinema.example" tvg-name="سينما العائلة" tvg-logo="" group-title="Arabic",سينما العائلة
https://tv.example.com/live/mah-7k3p9q/example-password/3002.ts
#EXTINF:-1 tvg-id="" tvg-name="The Matrix" tvg-logo="https://media.example.com/images/movie-1001/poster/w500.5d41402a.webp" group-title="Science Fiction",The Matrix
https://tv.example.com/movie/mah-7k3p9q/example-password/1001.mp4
#EXTINF:-1 tvg-id="" tvg-name="Breaking Bad S00E01" tvg-logo="https://media.example.com/images/series-2001/poster/w500.a87ff679.webp" group-title="Drama",Breaking Bad S00E01
https://tv.example.com/series/mah-7k3p9q/example-password/5003.mp4
```

The fixture's credentials are examples. A real playlist carries the customer's device credentials, so never log a playlist body or URL. The invalid cases `fixtures/invalid/m3u_plus--*.json` show each rule failing:

- the header (`missing-header`, `missing-url-tvg`);
- line endings (`crlf`);
- attributes (`missing-group-title`, `quote-in-name`, `name-differs-from-tvg-name`, `movie-tvg-id`);
- `#EXTINF` format (`duration-zero`, `vlc-option`, `extinf-without-url`);
- URLs (`relative-url`, `other-host`, `other-credentials`, `movie-ts-extension`, `live-m3u8-extension`);
- names and images (`episode-name-format`, `avif-logo`);
- the catalog (`movie-not-in-catalog`, `duplicate-entry`).
