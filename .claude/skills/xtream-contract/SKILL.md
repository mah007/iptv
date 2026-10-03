---
name: xtream-contract
description: Rules and workflow for anything that changes what IPTV apps receive from the Xtream host (tv.*). That covers player_api.php actions, get.php M3U, xmltv.php, and /movie, /series and /live play URLs, implemented in apps/xtream_api, compat/ schemas and fixtures, and the Xtream catalog cache. Use when adding or editing an Xtream action, serializer, fixture or contract test, or when debugging an IPTV app (Smarters, TiviMate, XCIPTV, OTT Navigator, IPTVnator) that won't log in, list content or play.
---

# Xtream contract

SPEC §7.5 is the source of truth; read the row for the action you're touching. This skill covers the traps and the workflow. Clients are strict about types: a field with the wrong type usually fails silently in the app (an empty list, or a spinner that never stops), not with an error.

## Types (PHP-panel compatible)
| Kind | Fields |
|---|---|
| **string** | `category_id` (everywhere), `exp_date`, `created_at`, `added`, `last_modified`, `start_timestamp`/`stop_timestamp`, `active_cons`, `max_connections`, `is_trial`, `status`, `port`/`https_port`/`rtmp_port`, `rating` in lists (`"8.2"`), `tmdb_id`, `tmdb`, `episode_run_time`, `is_adult`, episode `id`, `duration` (`"02:16:00"`) |
| **int** | `stream_id`, `series_id`, `season_number`, `episode_num`, episode `season`, `parent_id` (0), `timestamp_now`, `auth` |
| **number** | `rating_5based` |
| **bool** | `server_info.process` |
| **array** | `category_ids` (of ints), `backdrop_path` (always an array, even when empty), `allowed_output_formats` |
| **object** | `get_series_info.episodes`: keyed by season-number **strings**, including `"0"`; empty is `{}`, never `[]` |

Use `""`, never `null`, for every image and string field. `custom_sid` and `direct_source` are always `""`.

## Behaviour clients depend on
- Accept GET and POST, with credentials in the query or the form body.
- No action, an unknown action or `get_account_info` returns the login payload. `user_info.password` echoes the value the client sent.
- Failed auth returns `{"user_info":{"auth":0}}` with **HTTP 200**. The body and timing must be identical for an unknown user and a wrong password (constant-time compare, Argon2id), and the endpoint is rate limited per IP and per username.
- `get_vod_info` also duplicates the `info` fields at the root.
- Category names follow the user's locale (ar/en); lists are filtered by plan and ordered by `sort`.
- Play URLs check entitlement and take a slot, then return a **302** to the signed edge URL (https→https). The response body never contains storage keys or paths.
- Never log credentials. Redact `/movie/*/*/`, `/series/*/*/`, `/live/*/*/`, `password=` and `username=` in every log format.

## Change workflow
1. **Schema first.** Edit `compat/schemas/<action>.schema.json` with exact `type`s, `required` and `additionalProperties` as deliberate choices.
2. **Golden fixture.** Edit `compat/fixtures/<action>.json`, generated from seeded sample media rather than written by hand.
3. **Serializer.** Emit PHP types explicitly (`str(xc_id)`, `value or ""`). Don't rely on DRF field defaults. Serialize with orjson.
4. **Cache.** Output is cached per `xc:{plan_hash}:{locale}:{action}[:{category}]`. Every model whose change alters this output must invalidate it; add a test that edits one and checks the next response.
5. **Contract test.** Validate the live response against the schema and the fixture. A failure must print the JSON path and the type it found.
6. **End to end.** Run the IPTVnator Playwright job: log in, list categories, open a movie and a series, start playback.
7. **Breaking a contract?** Anything already shipped to clients counts as public, so ask the user first.

## Quick type audit against a running stack
```bash
# Credentials come from env vars; don't type them inline.
TV="${TV:-http://tv.localhost}"
xc() { curl -sG "$TV/player_api.php" --data-urlencode "username=$XC_USER" --data-urlencode "password=$XC_PASS" "$@"; }
xc | jq '{user_info: (.user_info | map_values(type)), server_info: (.server_info | map_values(type))}'
xc --data-urlencode action=get_vod_streams | jq '.[0] | map_values(type)'
xc --data-urlencode action=get_series_info --data-urlencode series_id=1 | jq '{episodes: (.episodes | type), keys: (.episodes | keys)}'
```
