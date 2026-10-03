# OTT Navigator

[All guides](README.md) · [العربية](../ar/ott-navigator.md)

> **Checked against** (3 October 2026): **OTT Navigator 1.7.5.2**, the developer's standalone build (28 September 2026), and the Google Play builds **OTT TV** and **OTT Player 1.7.4.1** (5 January 2026), together with the developer's FAQ at `ottnav.github.io`. Menus change between versions, so labels may differ slightly. Screenshots from real devices follow after device testing.

OTT Navigator runs on Android only: phones, tablets, Android TV, Google TV and Fire TV. It doesn't run on Samsung or LG TV systems.

## What you need

| Detail | Example |
|---|---|
| **Server URL** | `https://tv.example.com` |
| **Username** | `mah-7k3p9q` |
| **Password** | the 16 characters you were given |

You get these from your provider when your account is created, or yourself in the customer portal under **Account → Devices & TV apps → Add TV app**. Use a set of details made for this device only.

## Install the app

- **With Google Play:** the developer lists OTT Navigator under two names: **OTT TV** for TVs and **OTT Player** for phones and tablets, both by **AppNovatica SIA**.
- **Without Google Play** (for example Fire TV): the developer's FAQ at `ottnav.github.io` links to its standalone build.
- A different app called "OTT Navigator IPTV", from another developer, is also in Google Play. This guide doesn't cover it.

Some features need OTT Navigator's paid premium, which its developer sells, not us.

## Add your account (Xtream Codes)

1. Open the app. On first start it asks you to set up a provider. Otherwise, go to **Settings → Provider** and add a new provider.
2. Choose the **Xtream Codes** template (the one with a login and password).

   > **[Screenshot placeholder, M10]** `ottnav-androidtv-01-provider-template.png`: the provider templates, with **Xtream Codes** selected.

3. Fill in:
   - **Server:** `https://tv.example.com`
   - **Login:** your username
   - **Password:** your password
   - a provider name you'll recognise, for example `Home`

   > **[Screenshot placeholder, M10]** `ottnav-androidtv-02-xtream-form.png`: the filled-in provider form, with the password hidden.

4. Confirm. The app loads live TV, movies, series and the TV guide.
5. Movies and series are in their own sections of the main screen.

   > **[Screenshot placeholder, M10]** `ottnav-androidtv-03-movies.png`: the movies section after the first load.

**Typing tip:** a 16-character password is easier to type with your phone's keyboard, using the Google TV app (or your box's remote app) on the phone.

## Playlist link (M3U) fallback

Use this only if the Xtream Codes login doesn't work. Replace `USERNAME` and `PASSWORD` with your details:

```text
https://tv.example.com/get.php?username=USERNAME&password=PASSWORD&type=m3u_plus&output=ts
```

Add a provider with the **Playlist** template instead of **Xtream Codes**, and paste the link.

A playlist link loads more slowly than the Xtream Codes login and can show less detail (artwork, plots, seasons). It contains your password, so treat it like the password itself.

## Troubleshooting

### Login fails

- Check the server: exactly `https://tv.example.com`, beginning with `https://`, with no space, slash or path after it. If the app still refuses it, try `https://tv.example.com:443`.
- Type the login (username) and password exactly; both are case-sensitive.
- Look at the provider status under **Settings → Provider** and select your provider. It shows the error the server returned. If it says your account has expired or is disabled, your subscription has ended or your account is suspended: renew it, or contact support.
- After a password reset, or after this device was removed from your account, the old password stops working. Edit the provider and enter the new details.
- After several wrong attempts, the server slows down new attempts for a few minutes. Wait, then try once with the correct details.
- A certificate (SSL) error almost always means the device's date or time is wrong. Turn on automatic date and time. **Never** turn on the option to ignore invalid SSL certificates: it would let anyone on your network read your password.

### Categories are empty

- Reload the provider from **Settings → Provider**.
- Your subscription may not include every section (for example live TV or series) or every category. Ask support what yours includes.
- Live channels and the TV guide stay empty if your service doesn't offer live TV.
- Category names follow the language of your account, Arabic or English. You can change it in the customer portal under **Account**.

### Playback errors

- A title added very recently may still be in preparation. Try again later.
- Try the other decoder or an external player in **Settings → Player**.
- Check your internet speed: Full HD needs about 8 Mbps.
- 4K titles need a subscription that includes 4K and a device that can play 4K.
- If playback stops after a while, another device may have taken over your connection (see the next section), an administrator may have ended the session, or your subscription may have expired.
- Turn off any VPN or proxy. They can trigger the service's location rules or account-sharing checks.

### Too many connections

- Your subscription sets how many devices can watch at the same time. OTT Navigator reads this limit from the server.
- Stop playback on another device. After you stop, the connection can take up to 2 minutes to free up.
- Picture-in-picture, the multi-screen **Studio** mode and video previews in lists each use a connection. If previews cause errors, turn them off, or set **Settings → Provider →** (your provider) **→ Parameters → Number of connections** to your subscription's limit.
- Depending on your subscription, a new stream over the limit is either refused or stops your oldest stream.
- If you reach the limit while nobody else is watching, someone else may be using your details. Remove this device under **Account → Devices & TV apps**, add it again with new details, and tell support.

## Security notes

- These details belong to this device only. Don't reuse them on a second device and never share them: whoever has them watches on your account and uses up your connections. Shared or resold details are detected and can lead to a warning, a forced password reset or suspension.
- OTT Navigator's anonymous backups are restored with a code, and anyone who knows the code gets your settings, including your login. Don't share backup codes; prefer a backup tied to your own OTT Navigator account, or none.
- Never enable the setting that ignores invalid SSL certificates.
- The playlist link contains your password. Never post it, and never paste it into online playlist editors or converters.
- Install only from Google Play or the developer's own links. "Mod", "premium unlocked" and re-uploaded copies can steal your details.
- If you lose, sell or reset the device, remove it under **Account → Devices & TV apps** (or ask support). That revokes its details and leaves your other devices working.
- Our support team never asks for your password. If you need a new one, they reset it.
