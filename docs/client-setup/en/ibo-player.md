# IBO Player

[All guides](README.md) · [العربية](../ar/ibo-player.md)

> **Checked against** (3 October 2026): the IBO Player playlist manager at `iboplayer.com`, and **IBO PLAYER 2.8.2** for iPhone, iPad and Apple TV (App Store, 20 May 2026). Menus change between versions, so labels may differ slightly. Screenshots from real devices follow after device testing.

> **Important: IBO Player keeps your details on its developer's servers.** You don't type your username and password on the TV. You enter them on the IBO Player website, which stores them and sends them to your TV. Anyone who learns your TV's MAC address and device key can open that website and see or change them. Read the [security notes](#security-notes) before you continue. If you can, use an app that keeps your details on the device instead, such as [TiviMate](tivimate.md) on Android TV or [UHF](uhf.md) on Apple TV.

IBO Player runs on Samsung and LG smart TVs, Android TV, Fire TV and Apple devices. After a 7-day free trial, its developer charges a licence fee for each device. That fee goes to the app's developer, not to us.

## What you need

| Detail | Example |
|---|---|
| **Server URL** | `https://tv.example.com` |
| **Username** | `mah-7k3p9q` |
| **Password** | the 16 characters you were given |

You get these from your provider when your account is created, or yourself in the customer portal under **Account → Devices & TV apps → Add TV app**. Because this app stores them on a third-party server, use a set of details made for this TV only.

You also need a phone or computer with a web browser.

## Install the app

Install **IBO Player** from your TV's app store. Many unrelated apps copy the IBO name, so check the developer before installing. On Apple devices, the app checked for this guide is **IBO PLAYER** by **ibrahim akoum**, whose App Store listing links to the IBO Player website.

## Add your account (Xtream Codes)

1. Open IBO Player on the TV. Write down the **MAC address** and the **Device Key** that it shows.

   > **[Screenshot placeholder, M10]** `ibo-samsung-01-mac-and-key.png`: the IBO Player start screen showing the MAC address and device key (blur both before publishing).

2. On your phone or computer, open the website that the app shows on the TV, for example `iboplayer.com` (the Apple version uses `iboiptv.com`). Type the address yourself; don't follow links from adverts or search results, because copycat sites exist. Some app versions show a QR code that opens the right page for your TV.
3. Select **Manage Playlists**. Sign in with the **Mac Address** and **Device Key** from step 1, and complete the captcha.
4. Select **Add XC Playlist** and fill in:

   | Field | Enter |
   |---|---|
   | **Name** | a name for this account, for example `Home` |
   | **Host** | `https://tv.example.com` |
   | **Username** | your username |
   | **Password** | your password |
   | **XMLTV EPG URL (Optional)** | leave it empty |

   Then turn on **Protect this playlist** and choose a PIN. A protected playlist can't be viewed or changed on the website without the PIN.

   > **[Screenshot placeholder, M10]** `ibo-web-02-add-xc-playlist.png`: the **Add XC Playlist** form, filled in, with **Protect this playlist** turned on and the password hidden.

5. Select **ADD PLAYLIST**. The website confirms with **Playlist Added!**
6. On the TV, restart IBO Player. Your playlist appears, with live TV, movies and series.

   > **[Screenshot placeholder, M10]** `ibo-samsung-03-home.png`: IBO Player on the TV after the playlist loads.

## Playlist link (M3U) fallback

Use this only if the Xtream Codes entry doesn't work. Replace `USERNAME` and `PASSWORD` with your details:

```text
https://tv.example.com/get.php?username=USERNAME&password=PASSWORD&type=m3u_plus&output=ts
```

On the same website, select **Add Playlist** (instead of **Add XC Playlist**) and paste the link into **Playlist URL (.m3u / .m3u8)**. Turn on **Protect this playlist** here too, then restart the app on the TV.

A playlist link loads more slowly than the Xtream Codes entry and can show less detail (artwork, plots, seasons). It contains your password, so treat it like the password itself.

## Troubleshooting

### Login fails

- On the website, edit the playlist and check the **Host**: exactly `https://tv.example.com`, beginning with `https://`, with no space, slash or path after it.
- Check the username and password; both are case-sensitive.
- If the app says your account has expired or is disabled, your subscription has ended or your account is suspended. Renew it, or contact support.
- After a password reset, or after this TV was removed from your account, the old password stops working. Edit the playlist on the website with the new details, then restart the app.
- If the app is stuck on the loading screen, restart it two or three times. If that doesn't help, delete the playlist on the website, add it again and restart the app.
- If the app shows a different MAC address than before, the TV has probably switched between Wi-Fi and cable: most TVs have one MAC address for each. Use the address the app shows now, or use **Switch MAC** on the website.

### Categories are empty

- Restart the app on the TV so it loads the latest catalogue.
- Your subscription may not include every section (for example live TV or series) or every category. Ask support what yours includes.
- Live TV stays empty if your service doesn't offer live channels.
- Category names follow the language of your account, Arabic or English. You can change it in the customer portal under **Account**.

### Playback errors

- A title added very recently may still be in preparation. Try again later.
- Try the other player in the app's settings, if your version offers one.
- Check your internet speed: Full HD needs about 8 Mbps.
- 4K titles need a subscription that includes 4K and a TV that can play 4K.
- If playback stops after a while, another device may have taken over your connection (see the next section), an administrator may have ended the session, or your subscription may have expired.
- Turn off any VPN or proxy. They can trigger the service's location rules or account-sharing checks.

### Too many connections

- Your subscription sets how many devices can watch at the same time.
- Stop playback on another device. After you stop, the connection can take up to 2 minutes to free up.
- Depending on your subscription, a new stream over the limit is either refused or stops your oldest stream.
- If you reach the limit while nobody else is watching, someone else may be using your details, for example after reading them from the IBO Player website. Remove this TV under **Account → Devices & TV apps**, add it again with new details, protect the new playlist with a PIN, and tell support.

## Security notes

- **Your details live on the IBO Player developer's servers.** We can't protect them there. Always turn on **Protect this playlist** with a PIN, and keep the TV's device key private, just like a password.
- Use details made only for this TV, so they can be revoked on their own if that server is ever compromised.
- If you sell, give away or reset the TV, first delete the playlist on the IBO Player website, then remove the TV under **Account → Devices & TV apps**.
- If you think someone changed or viewed your playlist, remove the TV under **Account → Devices & TV apps** (this revokes its details), add it again with new details and a new PIN, and tell support.
- Never share your details or the playlist link. Shared or resold details are detected and can lead to a warning, a forced password reset or suspension.
- Our support team never asks for your password, your device key or your playlist PIN.
