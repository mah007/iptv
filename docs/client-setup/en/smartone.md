# SmartOne IPTV

[All guides](README.md) · [العربية](../ar/smartone.md)

> **Checked against** (3 October 2026): the SmartOne IPTV website `smartone-iptv.com` (its **Upload Playlist** page and FAQ) and the **SmartOne IPTV Player** listing in Google Play (developer **Smart Cast**, updated 7 August 2026). Menus change between versions, so labels may differ slightly. Screenshots from real devices follow after device testing.

> **Important: SmartOne keeps your details on its developer's servers.** You don't type your username and password on the TV. You enter them on the SmartOne website, which stores them and sends them to your TV. The website identifies your TV by its MAC address, which isn't a secret. Read the [security notes](#security-notes) before you continue. If you can, use an app that keeps your details on the device instead, such as [TiviMate](tivimate.md) on Android TV or [UHF](uhf.md) on Apple TV.

SmartOne runs on Samsung (Tizen and Orsay) and LG (webOS and Netcast) TVs, Hisense and other VIDAA TVs, Foxxum and Vestel-based TVs, Android TV and Fire TV. After a free trial of about two weeks, SmartOne's developer charges a licence fee for each TV, on the **Activation** page of its website. That fee goes to the app's developer, not to us.

## What you need

| Detail | Example |
|---|---|
| **Server URL** | `https://tv.example.com` |
| **Username** | `mah-7k3p9q` |
| **Password** | the 16 characters you were given |

You get these from your provider when your account is created, or yourself in the customer portal under **Account → Devices & TV apps → Add TV app**. Because this app stores them on a third-party server, use a set of details made for this TV only.

You also need a phone or computer with a web browser.

## Install the app

Install **SmartOne IPTV** from your TV's app store (on Android TV, the Google Play listing is **SmartOne IPTV Player** by **Smart Cast**).

## Add your account (Xtream Codes)

1. Open SmartOne IPTV on the TV and write down the **MAC address** it shows. Some LG webOS TVs show a device ID instead; use that.

   > **[Screenshot placeholder, M10]** `smartone-lg-01-mac.png`: the SmartOne start screen showing the MAC address (blur it before publishing).

2. On your phone or computer, go to `smartone-iptv.com` and select **Upload Playlist**. Type the address yourself; don't follow links from adverts or search results.
3. Open the **Xtream Server Info** tab and fill in:

   | Field | Enter |
   |---|---|
   | **Tv MAC** | the MAC address from step 1 |
   | **Account Name** | a name for this account, for example `Home` |
   | **Server Adress** (spelled this way on the site) | `https://tv.example.com` |
   | **Server Port** | `443` |
   | **Username** | your username |
   | **Password** | your password |

   > **[Screenshot placeholder, M10]** `smartone-web-02-xtream-server-info.png`: the **Xtream Server Info** tab, filled in, with the password hidden.

4. Select **Add Playlist**.
5. On the TV, restart SmartOne IPTV. It downloads your playlist automatically. You don't need to type the "smart key" the website shows: it only labels the playlist on the website.

   > **[Screenshot placeholder, M10]** `smartone-lg-03-home.png`: SmartOne on the TV after the playlist loads.

## Playlist link (M3U) fallback

Use this if the **Xtream Server Info** tab doesn't work. Replace `USERNAME` and `PASSWORD` with your details:

```text
https://tv.example.com/get.php?username=USERNAME&password=PASSWORD&type=m3u_plus&output=ts
```

On **Upload Playlist**, open the **Xtream Playlist** tab (meant for links like this one; the **M3u Playlist** tab is for plain playlists). Fill in **Tv MAC** and **Playlist Name**, paste the link into **Playlist**, select **Add Playlist** and restart the app on the TV.

A playlist link can load more slowly and show less detail (artwork, plots, seasons). It contains your password, so treat it like the password itself.

## Troubleshooting

### Login fails

- Check what you entered on the website: **Server Adress** exactly `https://tv.example.com`, **Server Port** `443`, and the username and password exactly as given (both are case-sensitive). To correct a mistake, add the playlist again with the right values.
- If the playlist still doesn't load, add it again on the **Xtream Playlist** tab with the full link (see the fallback above).
- If the app says your account has expired or is disabled, your subscription has ended or your account is suspended. Renew it, or contact support.
- After a password reset, or after this TV was removed from your account, the old password stops working. Add the playlist again on the website with the new details.
- A **Loading timeout** message means the TV couldn't reach the server in time. Restart your router and the TV, then open the app again. If it keeps happening, reinstall the app.
- If the app shows a different MAC address than before, the TV has probably switched between Wi-Fi and cable: most TVs have one MAC address for each. SmartOne activates the second one after the app restarts.

### Categories are empty

- Restart the app on the TV so it loads the latest catalogue.
- Your subscription may not include every section (for example live TV or series) or every category. Ask support what yours includes.
- Live TV stays empty if your service doesn't offer live channels.
- Category names follow the language of your account, Arabic or English. You can change it in the customer portal under **Account**.

### Playback errors

- A title added very recently may still be in preparation. Try again later.
- Check your internet speed: Full HD needs about 8 Mbps.
- 4K titles need a subscription that includes 4K and a TV that can play 4K.
- If playback stops after a while, another device may have taken over your connection (see the next section), an administrator may have ended the session, or your subscription may have expired.
- Turn off any VPN or proxy. They can trigger the service's location rules or account-sharing checks.

### Too many connections

- Your subscription sets how many devices can watch at the same time.
- Stop playback on another device. After you stop, the connection can take up to 2 minutes to free up.
- Depending on your subscription, a new stream over the limit is either refused or stops your oldest stream.
- If you reach the limit while nobody else is watching, someone else may be using your details. Remove this TV under **Account → Devices & TV apps**, add it again with new details, and tell support.

## Security notes

- **Your details live on SmartOne's servers**, and the website identifies your TV by its MAC address alone. We can't protect your details there.
- Use details made only for this TV, so they can be revoked on their own if that server is ever compromised.
- If you sell, give away or reset the TV, remove it under **Account → Devices & TV apps** first. That revokes the details stored at SmartOne, so they stop working for anyone.
- If you think someone else is using your details, remove the TV under **Account → Devices & TV apps**, add it again with new details, and tell support.
- Never share your details or the playlist link. Shared or resold details are detected and can lead to a warning, a forced password reset or suspension.
- Our support team never asks for your password.
