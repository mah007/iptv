# Client test matrix (M10)

SPEC §16 makes M10's acceptance a manual run of real IPTV apps against the Xtream host, with every issue fixed. The same run produces the screenshots for the `docs/client-setup/` guides (ar and en). Repeat this matrix before any release that changes what `tv.*` returns ([README](README.md)).

The automated gates come first. Before a device is picked up, these must be green:

- `validate.py --live --play` against the environment under test;
- the IPTVnator journey ([iptvnator.md](iptvnator.md)).

This matrix covers what only real apps show: their parsers, players, caches and TV-remote user interfaces.

## Environment

- **Host.** The production overlay on a staging domain: `https://tv.<staging-domain>` with a valid Let's Encrypt certificate (`docs/` runbook). Devices cannot reach `tv.localhost`. Apps build play URLs from what the tester types (and some from `server_info`), so `server_info` must name that same host, with `server_protocol` `https` and `https_port` `"443"`. Check this first with `validate.py --live`, whose `login … describes this server` check fails otherwise.
- **Network.**
  - Phones, TVs and boxes on ordinary Wi-Fi or Ethernet, with no VPN.
  - At least one run from a mobile network, which exercises a different IP and ASN for the anti-sharing rules in SPEC §11.1.
- **Catalog.** `make seed` and `make sample-media`, transcoded. It must contain:
  - at least three movie categories in a deliberate `sort` order;
  - a movie with a TMDB match, a movie without one, and a movie with an Arabic title;
  - a series with specials (season 0) and two regular seasons;
  - for M12: live channels with and without EPG and logos, one of them with catch-up.
- **Accounts.** Use test accounts only, never a customer's, and create each one's device credentials in the admin:

  | Account | State | Locale | `max_connections` | Used for |
  |---|---|---|---|---|
  | A | Active | en | 2 | Most checks |
  | B | Active | ar | 1 | Arabic names, the connection limit |
  | C | Expired | en | 1 | The expired state |
  | D | Disabled | en | 1 | The disabled state |
  | E | Active trial | en | 1 | `is_trial` |

- **Cost.** IBO Player and SmartOne charge a per-device activation after a free trial, and TiviMate keeps some features (catch-up, several playlists) for Premium. Get the owner's approval before buying anything (CLAUDE.md), and record which edition was tested.
- **Privacy.** IBO Player and SmartOne load playlists through their vendors' web portals, keyed by the device's MAC address. The portal then holds the account's credentials. Use test accounts there only, and state this plainly in the customer guides.

## The apps

Record the exact app version, device and OS version on every run. App labels change between versions; write down the labels you actually saw.

| ID | App | Platform for this run | How it connects |
|---|---|---|---|
| SMA | IPTV Smarters Pro | Android phone or Android TV | Xtream login |
| SMI | IPTV Smarters Pro | iPhone or iPad (and Apple TV if available) | Xtream login |
| TIV | TiviMate | Android TV or Google TV box | Xtream login and, separately, M3U URL |
| XCI | XCIPTV | Android phone or TV | Xtream login |
| OTT | OTT Navigator | Android phone or TV | Xtream login (provider "login and password") |
| UHF | UHF | iPhone or Apple TV | Xtream login and M3U URL |
| IBO | IBO Player | Android TV, or the TV app (see WEB) | Vendor portal: Xtream or M3U |
| SMO | SmartOne IPTV | Samsung Tizen or LG webOS TV | Vendor portal: M3U URL and XMLTV URL |
| WEB | IBO Player on LG webOS or Samsung Tizen | A real TV, not an emulator | Vendor portal (record which TV) |

### Adding the account

- **Xtream login** (SMA, SMI, XCI, OTT, UHF, and TIV with "Xtream Codes"):
  1. Choose the app's "Xtream Codes" or "Xtream API" option.
  2. Enter any playlist name, then the URL `https://tv.<staging-domain>` with no path and no port, then the device username and password.
  3. If the app offers an EPG URL field, leave it empty: the app finds the guide itself. M3U-based apps get it from `url-tvg`.
- **M3U URL** (TIV's M3U mode, UHF's M3U mode, SMO):
  1. Use `https://tv.<staging-domain>/get.php?username=<u>&password=<p>&type=m3u_plus&output=ts`.
  2. Where the app asks for an EPG URL, use `https://tv.<staging-domain>/xmltv.php?username=<u>&password=<p>`.
  3. Repeat once with `output=m3u8` in M12.
- **Vendor portal** (IBO, SMO, WEB):
  1. Open the app on the TV and note the MAC address (and device key) it shows.
  2. In the vendor's website, add the playlist for that MAC: Xtream (host, username, password) where offered, otherwise the M3U URL above.
  3. Restart or refresh the app on the TV.

## Checks

Run every check for every app, marking it Pass, Fail or N/A with a one-line note. N/A is only for a feature the app lacks or a milestone not yet built (M12 rows before live TV); write down which. A Fail is either our bug, fixed before M10 is accepted, or an app limitation, documented in the client guide with its workaround. Decide which with the contract: run `validate.py --live` and the skill's quick type audit. If our output is valid, the app is at fault.

| # | Check | Steps | Pass when |
|---|---|---|---|
| C1 | Login | Add account A as described above. | The app reaches its home screen with no error and no endless spinner. Where it shows account info, the expiry date matches the admin's subscription end, and the connection count reads 0 of 2. |
| C2 | Wrong password | Add account A with one wrong character in the password. | The app shows a login or authorization failure, with no crash and no endless spinner. Repeat with an unknown username: the behaviour is the same. |
| C3 | Expired and disabled | Add C, then D. | The app reports the account as expired or disabled, or refuses the login, and lists no playable content. |
| C4 | Categories | Open Movies (VOD), then Series. | Every category the plan allows appears, in the admin's `sort` order, and no others. For account B the names are Arabic and render correctly; note the text direction and any garbling. |
| C5 | Movie list | Open each movie category. | The counts match the admin. Posters load, or show the app's placeholder where we have none. Arabic titles render. Nothing is listed twice. |
| C6 | Movie details | Open The Matrix, or the seeded TMDB movie. | Every field the app shows is filled from our data: poster and backdrop, plot, genre, cast, director, year, rating and duration (`02:16:00` style). Note any field the app shows empty. |
| C7 | Movie playback | Play the movie. Seek forward about 10 minutes, then back. Pause and resume. Stop. | The first frame comes with no error, with sound. Seeks resume within a few seconds. While it plays, the admin's live-sessions view shows one session for this device; after Stop it ends. Note the time to first frame. |
| C8 | Series | Open the seeded series. | Seasons appear in order, including Specials (season 0) where the app supports it. Episode numbers and titles are right, and each season lists the episodes the admin shows. |
| C9 | Episode playback | Play S01E01, then the app's "next episode" if it has one. | As in C7, and the next episode starts correctly. |
| C10 | Search | Use the app's search for an English title and an Arabic title. | Both are found. App search runs on the device, so a miss here is usually an app limitation, but record it. |
| C11 | Connection limit | With account B (`max_connections` 1), play a movie on device 1, then start any title on device 2. | Device 2 is refused, showing the app's playback error, while device 1 keeps playing. After device 1 stops and the session expires, device 2 can play. |
| C12 | Catalog change | In the admin, unpublish a movie and hide a category from the plan. Refresh the playlist in the app (or restart it). | The movie and the category are gone. Re-publish them: they come back. This proves the cache is invalidated (SPEC §7.5). |
| C13 | M3U | M3U-mode apps only (TIV, UHF, SMO). | The playlist loads. Groups are our category names. Movies and episodes play. Episode names read `Series S01E02`, and channel logos appear (M12). |
| C14 | Live (M12) | Open live TV and play two channels in TS and in HLS (`output=m3u8`). | Channels play, and zapping works. |
| C15 | Guide (M12) | Open the guide and the now/next banner. | Programme titles (Arabic included), times in the device's local time, and the current programme are right. |
| C16 | Catch-up (M12) | Open a finished programme on the catch-up channel. | It plays from its start. Programmes that have not ended offer no catch-up. |
| C17 | Resilience | Turn Wi-Fi off for 10 seconds during playback, then back on. Send the app to the background for 5 minutes and return. | Playback recovers, or the app retries cleanly. On return there is no re-login loop and no stuck spinner. |

## Recording results

Keep the run in `docs/client-setup/matrix-<date>.md` (M10). Use one row per app and one column per check:

```text
| App | Version | Device / OS | Tester | Date | C1 | C2 | … | C17 | Issues |
|-----|---------|-------------|--------|------|----|----|---|-----|--------|
| SMA | 5.x.y   | Shield TV / Android 11 | … | 2026-… | Pass | Pass | … | N/A (M12) | #123 |
```

- **Screenshots.** Take them of login, the categories, movie details, playback, the series and the seasons, with the account's username and password blurred. They are the source for the ar and en client guides; Arabic shots come from account B.
- **Issues.** Every Fail links an issue. M10 is accepted when every Fail is either fixed and re-tested, or documented as an app limitation in that app's guide.
