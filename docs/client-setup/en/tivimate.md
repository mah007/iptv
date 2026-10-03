# TiviMate

[All guides](README.md) · [العربية](../ar/tivimate.md)

> **Checked against** (3 October 2026): **TiviMate IPTV Player 5.3.3** from Google Play (updated 22 June 2026), developer **Armobsoft FZE**. Menus change between versions, so labels may differ slightly. Screenshots from real devices follow after device testing.

TiviMate is made for Android TV and Google TV and is driven with the TV remote. It isn't designed for phones or tablets.

## What you need

| Detail | Example |
|---|---|
| **Server URL** | `https://tv.example.com` |
| **Username** | `mah-7k3p9q` |
| **Password** | the 16 characters you were given |

You get these from your provider when your account is created, or yourself in the customer portal under **Account → Devices & TV apps → Add TV app**. Use a set of details made for this device only.

## Install the app

On your Android TV or Google TV device, install **TiviMate IPTV Player** from Google Play. Check that the developer is **Armobsoft FZE**.

TiviMate offers a paid **Premium** upgrade with extra features. It is sold by TiviMate's developer, not by us, and you don't need it to add one account.

## Add your account (Xtream Codes)

1. Open TiviMate and select **Add playlist**. If you already have a playlist, go to **Settings → Playlists → Add playlist** instead.
2. Choose **Xtream codes**.

   > **[Screenshot placeholder, M10]** `tivimate-androidtv-01-playlist-type.png`: the playlist types, with **Xtream codes** selected.

3. Fill in:
   - **Server address:** `https://tv.example.com`
   - **Username:** your username
   - **Password:** your password

   > **[Screenshot placeholder, M10]** `tivimate-androidtv-02-xtream-form.png`: the filled-in form, with the password hidden.

4. Select **Next**. TiviMate checks your details and shows how many channels, movies and series it found.
5. Give the playlist a name, for example `Home`, and select **Done**.
6. TiviMate opens. Press **Back** or **Left** on the remote to open the main menu, where you'll find **Movies** and your other sections.

   > **[Screenshot placeholder, M10]** `tivimate-androidtv-03-movies.png`: the Movies section after the first load.

**Typing tip:** a 16-character password is easier to type with your phone. The Google TV app (or the Android TV remote app) on your phone lets you type with the phone's keyboard.

## Playlist link (M3U) fallback

Use this only if the Xtream codes login doesn't work. Replace `USERNAME` and `PASSWORD` with your details:

```text
https://tv.example.com/get.php?username=USERNAME&password=PASSWORD&type=m3u_plus&output=ts
```

In **Add playlist**, choose the playlist URL (M3U) option instead of **Xtream codes**, enter the link and select **Next**.

A playlist link loads more slowly than the Xtream codes login and can show less detail (artwork, plots, seasons). It contains your password, so treat it like the password itself.

## Troubleshooting

### Login fails

- Check the server address: exactly `https://tv.example.com`, beginning with `https://`, with no space, slash or path after it. If TiviMate still refuses it, try `https://tv.example.com:443`.
- Type the username and password exactly; both are case-sensitive.
- If TiviMate reports that your account has expired or is disabled, your subscription has ended or your account is suspended. Renew it, or contact support.
- After a password reset, or after this device was removed from your account, the old password stops working. Edit the playlist under **Settings → Playlists** and enter the new details.
- After several wrong attempts, the server slows down new attempts for a few minutes. Wait, then try once with the correct details.
- A wrong date or time on the device breaks secure connections. Turn on automatic date and time in the device settings.

### Categories are empty

- Update the playlist: **Settings → Playlists**, select your playlist, then **Update playlist**.
- Your subscription may not include every section (for example live TV or series) or every category. Ask support what yours includes.
- Live channels and the TV guide stay empty if your service doesn't offer live TV. Movies and series still appear in their own sections.
- Category names follow the language of your account, Arabic or English. You can change it in the customer portal under **Account**.

### Playback errors

- A title added very recently may still be in preparation. Try again later.
- Check your internet speed: Full HD needs about 8 Mbps.
- 4K titles need a subscription that includes 4K and a TV or box that can play 4K.
- If playback stops after a while, another device may have taken over your connection (see the next section), an administrator may have ended the session, or your subscription may have expired.
- Turn off any VPN or proxy. They can trigger the service's location rules or account-sharing checks.

### Too many connections

- Your subscription sets how many devices can watch at the same time.
- Stop playback on another device. After you stop, the connection can take up to 2 minutes to free up.
- **Multiview** uses one connection for each screen, and a **recording** uses a connection while it runs.
- Depending on your subscription, a new stream over the limit is either refused or stops your oldest stream.
- If you reach the limit while nobody else is watching, someone else may be using your details. Remove this device under **Account → Devices & TV apps**, add it again with new details, and tell support.

## Security notes

- These details belong to this device only. Don't reuse them on a second device and never share them: whoever has them watches on your account and uses up your connections. Shared or resold details are detected and can lead to a warning, a forced password reset or suspension.
- The playlist link contains your password. Never post it, and never paste it into online playlist editors or converters.
- Install TiviMate only from Google Play. "Mod", "premium unlocked" and re-uploaded copies can steal your details.
- If you lose, sell or reset the device, remove it under **Account → Devices & TV apps** (or ask support). That revokes its details and leaves your other devices working.
- Our support team never asks for your password. If you need a new one, they reset it.
