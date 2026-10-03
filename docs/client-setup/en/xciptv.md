# XCIPTV Player

[All guides](README.md) · [العربية](../ar/xciptv.md)

> **Checked against** (3 October 2026): **XCIPTV PLAYER 7.0** from Google Play (updated 12 August 2026), developer **OTTRUN**. Menus change between versions, so labels may differ slightly. Screenshots from real devices follow after device testing.

XCIPTV runs on Android phones, tablets and Android TV. Some providers hand out their own rebranded copies of XCIPTV with a fixed server; this guide is for the standard **XCIPTV PLAYER** app from Google Play.

## What you need

| Detail | Example |
|---|---|
| **Server URL** | `https://tv.example.com` |
| **Username** | `mah-7k3p9q` |
| **Password** | the 16 characters you were given |

You get these from your provider when your account is created, or yourself in the customer portal under **Account → Devices & TV apps → Add TV app**. Use a set of details made for this device only.

## Install the app

Install **XCIPTV PLAYER** from Google Play. Check that the developer is **OTTRUN**.

## Add your account (Xtream Codes)

1. Open XCIPTV PLAYER. On the login screen, open the panel type list. In the **SELECT PANEL TYPE** window, choose **Xtream Codes API**.

   > **[Screenshot placeholder, M10]** `xciptv-androidtv-01-panel-type.png`: the **SELECT PANEL TYPE** window, with **Xtream Codes API** selected.

2. Enter your username and your password, and enter `https://tv.example.com` as the server URL.

   > **[Screenshot placeholder, M10]** `xciptv-androidtv-02-login.png`: the filled-in login screen, with the password hidden.

3. Select **Sign In**. XCIPTV downloads live TV, movies, series and the TV guide. The first load can take a minute.
4. The home screen shows **LIVE TV**, **EPG**, **VOD** and **SERIES**.

   > **[Screenshot placeholder, M10]** `xciptv-androidtv-03-home.png`: the home screen after login.

## Playlist link (M3U) fallback

Use this only if the Xtream Codes login doesn't work. Replace `USERNAME` and `PASSWORD` with your details:

```text
https://tv.example.com/get.php?username=USERNAME&password=PASSWORD&type=m3u_plus&output=ts
```

In the **SELECT PANEL TYPE** window, choose **M3U URL** instead of **Xtream Codes API**, paste the link and select **Sign In**.

A playlist link loads more slowly than the Xtream Codes login and can show less detail (artwork, plots, seasons). It contains your password, so treat it like the password itself.

## Troubleshooting

### Login fails

- Check the server URL: exactly `https://tv.example.com`, beginning with `https://`, with no space, slash or path after it. If XCIPTV still refuses it, try `https://tv.example.com:443`.
- Check that the panel type is **Xtream Codes API**, not **EZHometech API**.
- Type the username and password exactly; both are case-sensitive.
- If the app says your account has expired or is disabled, your subscription has ended or your account is suspended. Renew it, or contact support.
- After a password reset, or after this device was removed from your account, the old password stops working. Sign out (**SETTINGS**, then the sign-out button) and sign in with the new details.
- After several wrong attempts, the server slows down new attempts for a few minutes. Wait, then try once with the correct details.
- A wrong date or time on the device breaks secure connections. Turn on automatic date and time.

### Categories are empty

- Reload the catalogue: **SETTINGS → Update Contents**.
- Your subscription may not include every section (for example **LIVE TV** or **SERIES**) or every category. Ask support what yours includes.
- **LIVE TV** and **EPG** stay empty if your service doesn't offer live TV.
- Category names follow the language of your account, Arabic or English. You can change it in the customer portal under **Account**.

### Playback errors

- A title added very recently may still be in preparation. Try again later.
- Switch the player: **SETTINGS → Player** lets you pick **EXO Player** or **VLC Player** separately for Live TV, VOD and Series. If a movie won't play, switch **VOD** to the other player.
- Check your internet speed with **SETTINGS → Speed Test**: Full HD needs about 8 Mbps.
- 4K titles need a subscription that includes 4K and a device that can play 4K.
- If playback stops after a while, another device may have taken over your connection (see the next section), an administrator may have ended the session, or your subscription may have expired.
- Keep the app's **VPN** turned off, and don't use another VPN or proxy. They can trigger the service's location rules or account-sharing checks.

### Too many connections

- Your subscription sets how many devices can watch at the same time.
- Stop playback on another device. After you stop, the connection can take up to 2 minutes to free up.
- **MULTI** (multi-screen) uses one connection for each screen, and a recording uses a connection while it runs.
- Depending on your subscription, a new stream over the limit is either refused or stops your oldest stream.
- If you reach the limit while nobody else is watching, someone else may be using your details. Remove this device under **Account → Devices & TV apps**, add it again with new details, and tell support.

## Security notes

- These details belong to this device only. Don't reuse them on a second device and never share them: whoever has them watches on your account and uses up your connections. Shared or resold details are detected and can lead to a warning, a forced password reset or suspension.
- **Backup & Restore → Cloud Backup** stores your app settings on the app developer's servers, linked to your login. Use it only if you accept that, and remember that it is outside our control.
- The playlist link contains your password. Never post it, and never paste it into online playlist editors or converters.
- Install XCIPTV only from Google Play. "Mod", "premium unlocked" and re-uploaded copies can steal your details.
- If you lose, sell or reset the device, remove it under **Account → Devices & TV apps** (or ask support). That revokes its details and leaves your other devices working.
- Our support team never asks for your password. If you need a new one, they reset it.
