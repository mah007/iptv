# IPTV Smarters Pro and Smarters Player Lite

[All guides](README.md) · [العربية](../ar/iptv-smarters.md)

> **Checked against** (3 October 2026): **IPTV Smarters Pro 5.0** for Android, Android TV and Fire TV, from the developer's download page, and **Smarters Player Lite 1.4.2** for iPhone, iPad, Apple TV and Mac (App Store, released 11 September 2026). Menus change between versions, so labels may differ slightly. Screenshots from real devices follow after device testing.

Both apps come from the same developer, **WHMCS Smarters**. On Apple devices the app is called **Smarters Player Lite**.

## What you need

| Detail | Example |
|---|---|
| **Server URL** | `https://tv.example.com` |
| **Username** | `mah-7k3p9q` |
| **Password** | the 16 characters you were given |

You get these from your provider when your account is created, or yourself in the customer portal under **Account → Devices & TV apps → Add TV app**. Use a set of details made for this device only.

## Install the app

- **iPhone, iPad, Apple TV and Mac:** install **Smarters Player Lite** from the App Store. Check that the developer is **WHMCS SMARTERS**: several unrelated apps copy the name.
- **Android phone, Android TV and Fire TV:** at the time of writing, IPTV Smarters Pro is in neither Google Play nor the Amazon Appstore. The developer offers it only as a download from its own website, the one its App Store listing links to (`getiptvsmarters.com` on 3 October 2026). An app installed this way doesn't update itself, and you have to trust the download. If your device has Google Play, [TiviMate](tivimate.md), [XCIPTV](xciptv.md) and [OTT Navigator](ott-navigator.md) install straight from the store.

## Add your account (Xtream Codes)

### Android, Android TV and Fire TV

1. Open **IPTV Smarters Pro** and accept the terms of use.
2. Choose **Login with Xtream Codes API**. If the app first shows a list of users, select **Add user**.

   > **[Screenshot placeholder, M10]** `smarters-android-01-login-methods.png`: the list of login methods, with **Login with Xtream Codes API** selected.

3. Fill in the form:

   | Field | Enter |
   |---|---|
   | **Any Name** | a name for this account, for example `Home` |
   | **Username** | your username |
   | **Password** | your password |
   | **URL** (the box that shows `http://url_here.com:port`) | `https://tv.example.com` |

   > **[Screenshot placeholder, M10]** `smarters-android-02-xtream-form.png`: the filled-in form, with the password hidden.

4. Select **Add User**. The app checks your details and downloads the catalogue. The first load can take a minute.
5. The home screen shows **Live TV**, **Movies** and **Series**.

   > **[Screenshot placeholder, M10]** `smarters-android-03-home.png`: the home screen after login.

### iPhone, iPad, Apple TV and Mac (Smarters Player Lite)

1. Open **Smarters Player Lite**.
2. On **Enter your login details**, fill in **Any Name**, **Username** and **Password**. In the box that shows `http://url_here.com:port`, enter `https://tv.example.com`.

   > **[Screenshot placeholder, M10]** `smarters-appletv-01-login.png`: the login form on Apple TV.

3. Select **Add User**.
4. The home screen shows **Live**, **Movies** and **Series**. To switch between saved accounts later, use **List Users**.

   > **[Screenshot placeholder, M10]** `smarters-appletv-02-home.png`: the home screen on Apple TV.

**Tip for Apple TV:** typing a 16-character password with the Siri Remote is slow. Use the Apple TV Remote in your iPhone's Control Centre, which brings up the iPhone keyboard.

## Playlist link (M3U) fallback

Use this only if the Xtream Codes login doesn't work in your app. Replace `USERNAME` and `PASSWORD` with your details:

```text
https://tv.example.com/get.php?username=USERNAME&password=PASSWORD&type=m3u_plus&output=ts
```

In IPTV Smarters Pro, choose **Load Your Playlist or File/URL** instead of the Xtream Codes login, select **M3U URL**, give it a name and paste the link. Smarters Player Lite has a playlist option on the same screen.

A playlist link loads more slowly than the Xtream Codes login and can show less detail (artwork, plots, seasons). It contains your password, so treat it like the password itself.

## Troubleshooting

### Login fails

- Check the server URL: exactly `https://tv.example.com`, beginning with `https://`, with no space, slash or path after it. If the app still refuses it, try `https://tv.example.com:443`.
- Type the username and password exactly; both are case-sensitive. Copied text often carries an extra space at the end.
- If the app says your account is **Expired** or **Disabled**, your subscription has ended or your account is suspended. Renew it, or contact support.
- After a password reset, or after this device was removed from your account, the old password stops working. Use the new details.
- After several wrong attempts, the server slows down new attempts for a few minutes. Wait, then try once with the correct details.
- A wrong date or time on the device breaks secure connections. Turn on automatic date and time.
- Older TVs and boxes with out-of-date software can fail to open secure (`https`) connections. Update the device software.

### Categories are empty

- Load the latest catalogue: use the refresh button on the home screen, or log out and in again.
- Your subscription may not include every section (for example **Live TV** or **Series**) or every category. Ask support what yours includes.
- **Live TV** stays empty if your service doesn't offer live channels.
- Category names follow the language of your account, Arabic or English. You can change it in the customer portal under **Account**.

### Playback errors

- A title added very recently may still be in preparation. Try again later.
- In **Settings**, switch to the other player (the built-in player or VLC), then try again.
- Check your internet speed: Full HD needs about 8 Mbps.
- 4K titles need a subscription that includes 4K and a device that can play 4K.
- If playback stops after a while, another device may have taken over your connection (see the next section), an administrator may have ended the session, or your subscription may have expired.
- Turn off any VPN or proxy. They can trigger the service's location rules or account-sharing checks.

### Too many connections

- Your subscription sets how many devices can watch at the same time. In the app, **Account** shows your limit (maximum connections) and how many are in use (active connections).
- Stop playback on another device. After you stop, the connection can take up to 2 minutes to free up.
- Multi-screen viewing uses one connection for each screen.
- Depending on your subscription, a new stream over the limit is either refused or stops your oldest stream.
- If you reach the limit while nobody else is watching, someone else may be using your details. Remove this device under **Account → Devices & TV apps**, add it again with new details, and tell support.

## Security notes

- These details belong to this device only. Don't reuse them on a second device and never share them: whoever has them watches on your account and uses up your connections. Shared or resold details are detected and can lead to a warning, a forced password reset or suspension.
- The playlist link contains your password. Never post it, and never paste it into online playlist editors or converters.
- Install only from the App Store or the developer's own download page. "Mod", "premium unlocked" and re-uploaded copies can steal your details.
- If you lose, sell or stop using the device, remove it under **Account → Devices & TV apps** (or ask support). That revokes its details and leaves your other devices working.
- Our support team never asks for your password. If you need a new one, they reset it.
