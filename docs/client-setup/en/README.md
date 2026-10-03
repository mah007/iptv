# Connect an IPTV app

[العربية](../ar/README.md)

These guides show how to connect an IPTV app on your TV, streaming box, phone or tablet to your Smart IPTV subscription. Each guide covers one app: what you need, where to enter it, a playlist-link fallback, troubleshooting and security notes.

## What you need

Three details, which your provider gives you when your account is created:

| Detail | Example | Notes |
|---|---|---|
| **Server URL** | `https://tv.example.com` | Always starts with `https://`. Nothing comes after the name: no port, no slash, no path. |
| **Username** | `mah-7k3p9q` | Type it exactly as given. |
| **Password** | 16 characters | Case-sensitive. It's shown only once, so keep it somewhere safe. |

You get them in one of two ways:

- **From your provider**, on screen, on a printed setup card or in a message when your account is created.
- **Yourself, in the customer portal** (where your provider offers it): **Account → Devices & TV apps → Add TV app**. The portal shows the server URL, username and password once, with a QR code.

**One set of details per device.** Every TV, box or app gets its own username and password. If you add a second device, create a new set for it instead of reusing these. You can then remove any one device without disturbing the others.

## Choose your app

| Guide | Runs on | Your details are stored |
|---|---|---|
| [IPTV Smarters Pro and Smarters Player Lite](iptv-smarters.md) | Android phones and TVs, Fire TV; iPhone, iPad, Apple TV, Mac | On your device |
| [TiviMate](tivimate.md) | Android TV and Google TV | On your device |
| [XCIPTV Player](xciptv.md) | Android phones, tablets and TVs | On your device |
| [OTT Navigator](ott-navigator.md) | Android phones, tablets and TVs, Fire TV | On your device |
| [UHF](uhf.md) | iPhone, iPad, Apple TV, Mac | On your device |
| [IBO Player](ibo-player.md) | Samsung, LG, Android TV, Fire TV, Apple devices | **On the app vendor's servers** |
| [SmartOne IPTV](smartone.md) | Samsung, LG, Hisense (VIDAA) and other smart TVs, Android TV, Fire TV | **On the app vendor's servers** |

**Which one should I pick?**

- **Android TV, Google TV or a streaming box with Google Play:** TiviMate, OTT Navigator or XCIPTV.
- **Apple TV, iPhone or iPad:** UHF or Smarters Player Lite.
- **Samsung or LG smart TV:** IBO Player or SmartOne. These are MAC-activated: you enter your details on the vendor's website, which keeps them and sends them to your TV. If you'd rather keep your details on your own device, connect an Android TV stick, a Fire TV stick or an Apple TV to the television instead.

All of these apps are made by other companies. We don't control them, and some charge their own fee after a trial.

## Status of these guides

- **Written:** 3 October 2026. Each guide names the app version and source it was checked against.
- **Screenshots:** marked *Screenshot placeholder* in the text. They are taken on real devices during client testing (M10).
- **Server side:** the Xtream API that these apps talk to arrives in M9; the guides describe how it will behave.

## For operators

- **Replace the example host.** The guides use `https://tv.example.com`. Before sharing them, replace it with your Xtream host (`TV_HOST`, see the [deploy runbook](../../runbooks/deploy.md)), and keep `https://`.
- **Where these guides are used:** the customer portal's illustrated setup guides under *Devices & TV apps* and the admin's per-app quick guides. Keep the app list in sync with them.
- **Screenshots:** save them in `docs/client-setup/screenshots/` using the file name given in each placeholder (`<app>-<platform>-<step>-<what>.png`), then replace the placeholder with the image. Crop out usernames, passwords, QR codes and MAC addresses, or use a throwaway test credential that you revoke afterwards.
- **Verify on real devices in M10:**
  - `https://` without a port works in every app; if one needs it, document `https://tv.example.com:443` for that app;
  - SmartOne accepts `https` with port `443` on its *Xtream Server Info* tab;
  - IBO Player accepts an `https` host;
  - TiviMate and the others behave sensibly when a customer has movies and series but no live channels;
  - the error each app shows for an expired account, a revoked credential and the connection limit.
- **Credential hygiene:** MAC-activated apps send the credential to a third-party server. Customers who use them should get a dedicated credential that is easy to revoke. See the abuse cases in the [threat model](../../security/threat-model.md).
