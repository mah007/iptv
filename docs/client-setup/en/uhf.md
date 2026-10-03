# UHF (Apple TV, iPhone, iPad, Mac)

[All guides](README.md) · [العربية](../ar/uhf.md)

> **Checked against** (3 October 2026): **UHF - Love your IPTV 1.101.2** from the App Store (updated 26 August 2026), developer **Short Wavelength Applications**. Menus change between versions, so labels may differ slightly. Screenshots from real devices follow after device testing.

UHF runs on Apple TV, iPhone, iPad and Mac. It's free, with an optional **PRO** subscription sold by its developer, not by us.

## What you need

| Detail | Example |
|---|---|
| **Server URL** | `https://tv.example.com` |
| **Username** | `mah-7k3p9q` |
| **Password** | the 16 characters you were given |

You get these from your provider when your account is created, or yourself in the customer portal under **Account → Devices & TV apps → Add TV app**. Use a set of details made for this device only.

## Install the app

Install **UHF - Love your IPTV** from the App Store on the device you'll watch on. Check that the developer is **Short Wavelength Applications**.

## Add your account (Xtream Codes)

1. Open UHF and select **Add playlist**. On first start UHF offers it straight away; later you'll find it at the bottom of the sidebar.
2. Choose **Xtream** as the playlist type.

   > **[Screenshot placeholder, M10]** `uhf-appletv-01-playlist-type.png`: the playlist types, with **Xtream** selected.

3. Fill in a name for the playlist (for example `Home`), the server `https://tv.example.com`, your username and your password.

   > **[Screenshot placeholder, M10]** `uhf-appletv-02-xtream-form.png`: the filled-in form, with the password hidden.

4. Save. UHF loads live TV, movies and series, and adds the playlist to the sidebar.

   > **[Screenshot placeholder, M10]** `uhf-appletv-03-home.png`: the home screen with the new playlist in the sidebar.

**Tip for Apple TV:** typing a 16-character password with the Siri Remote is slow. Use the Apple TV Remote in your iPhone's Control Centre, which brings up the iPhone keyboard.

**One device, one set of details.** UHF PRO can sync your playlists between your Apple devices. Synced devices share the same username and password, so our service sees them as one device and you can't remove them separately. For each extra Apple device, add a separate TV app in the customer portal and enter its own details on that device.

## Playlist link (M3U) fallback

Use this only if the Xtream login doesn't work. Replace `USERNAME` and `PASSWORD` with your details:

```text
https://tv.example.com/get.php?username=USERNAME&password=PASSWORD&type=m3u_plus&output=ts
```

In **Add playlist**, choose the M3U playlist type instead of **Xtream** and paste the link.

A playlist link loads more slowly than the Xtream login and can show less detail (artwork, plots, seasons). It contains your password, so treat it like the password itself.

## Troubleshooting

### Login fails

- Check the server: exactly `https://tv.example.com`, beginning with `https://`, with no space, slash or path after it. If UHF still refuses it, try `https://tv.example.com:443`.
- Type the username and password exactly; both are case-sensitive.
- If UHF reports that your account has expired or is disabled, your subscription has ended or your account is suspended. Renew it, or contact support.
- After a password reset, or after this device was removed from your account, the old password stops working. Edit the playlist and enter the new details.
- After several wrong attempts, the server slows down new attempts for a few minutes. Wait, then try once with the correct details.
- A wrong date or time on the device breaks secure connections. Turn on **Set Automatically** for date and time.

### Categories are empty

- Refresh the playlist from its settings, or remove it and add it again.
- Your subscription may not include every section (for example live TV or series) or every category. Ask support what yours includes.
- Live TV and the TV guide stay empty if your service doesn't offer live TV.
- Category names follow the language of your account, Arabic or English. You can change it in the customer portal under **Account**.

### Playback errors

- A title added very recently may still be in preparation. Try again later.
- Check your internet speed: Full HD needs about 8 Mbps.
- 4K titles need a subscription that includes 4K and an Apple TV 4K.
- If playback stops after a while, another device may have taken over your connection (see the next section), an administrator may have ended the session, or your subscription may have expired.
- Turn off UHF's built-in **VPN** for this playlist, and any other VPN or proxy. They can trigger the service's location rules or account-sharing checks.

### Too many connections

- Your subscription sets how many devices can watch at the same time.
- Stop playback on another device. After you stop, the connection can take up to 2 minutes to free up.
- Multi picture-in-picture uses one connection for each picture, and a recording uses a connection while it runs.
- Depending on your subscription, a new stream over the limit is either refused or stops your oldest stream.
- If you reach the limit while nobody else is watching, someone else may be using your details. Remove this device under **Account → Devices & TV apps**, add it again with new details, and tell support.

## Security notes

- These details belong to this device only. Don't reuse them on a second device and never share them: whoever has them watches on your account and uses up your connections. Shared or resold details are detected and can lead to a warning, a forced password reset or suspension.
- Syncing a playlist through UHF PRO copies your details to every device signed in to your UHF account. Prefer separate details per device (see above).
- The playlist link contains your password. Never post it, and never paste it into online playlist editors or converters.
- Install UHF only from the App Store.
- If you lose, sell or reset the device, remove it under **Account → Devices & TV apps** (or ask support). That revokes its details and leaves your other devices working.
- Our support team never asks for your password. If you need a new one, they reset it.
