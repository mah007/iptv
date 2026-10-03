# xmltv.php: the XMLTV guide

The playlist's `url-tvg` points here, and apps such as TiviMate and OTT Navigator load it for the full programme guide. Xtream-login apps also read guide data per channel through `get_short_epg` and `get_simple_data_table`. Both views come from the same `EpgProgram` rows (SPEC §6 live, M12).

- **Golden file:** `fixtures/xmltv.xml`.
- **Checker:** `check_xmltv()` in `validate.py`.
- **Labels in this page:**
  - **[checked]** means `validate.py` fails a guide that breaks the rule.
  - **[recommended]** marks behaviour the checker does not enforce yet.

## Request

```text
GET /xmltv.php?username=<u>&password=<p>
```

- **Credentials** are in the query. Failed authentication follows the same rules as `get.php` ([m3u.md](m3u.md)).
- **Before M12** the account has no channels. The response is then a valid, empty guide, not an error: `<tv generator-info-name="Smart IPTV"></tv>`. Apps fetch `url-tvg` on their own, and a 404 there shows as a guide error. **[recommended]**

## Response

- **Status and media type.** `200` with `Content-Type: application/xml` or `text/xml` (`charset=utf-8`). **[checked]**
- **Compression.** Use gzip when `Accept-Encoding` allows it (SPEC §7.5). **[recommended]**; the checker decodes gzip.
- **Encoding.** UTF-8. If the XML declaration names an encoding, it is `UTF-8`. **[checked]**
- **No DTD.** No `<!DOCTYPE …[ … ]>` internal subset and no `<!ENTITY>` declarations, because XMLTV needs neither and client parsers should never meet them. **[checked]**
- **Structure.** Well-formed, with the root `<tv>`. Its only children are `<channel>` and `<programme>`, and every `<channel>` comes before the first `<programme>`. **[checked]**

### `<channel>`

- **`id`.** Non-empty, without whitespace, unique. It is the channel's `epg_channel_id`, which is the same string as `tvg-id` in the playlist and `channel_id` in the EPG actions. **[checked]**
- **Scope.** The guide lists only the account's channels: every channel id belongs to one of its `get_live_streams`. **[checked]** (live run)
- **`<display-name>`.** At least one, non-empty. **[checked]** One per language, with `lang="en"` or `lang="ar"`. **[recommended]**
- **`<icon src>`.** Optional; when present, an absolute `http(s)` URL. **[checked]** Use the same image as `stream_icon`. **[recommended]**

### `<programme>`

- **Times.** `start` and `stop` are written `YYYYMMDDhhmmss +0000`: always UTC, like every Xtream time (`server_info.timezone` is `"UTC"`). They are real times, and `stop` is after `start`. **[checked]**
- **`channel`.** Names a declared `<channel>`. **[checked]**
- **`<title>`.** At least one, non-empty. **[checked]** Write `title` with `lang="en"` and `title_ar`, when present, with `lang="ar"`. `<desc lang>` is optional. **[recommended]**
- **Order.** Programmes are grouped by channel and ordered by `start`. **[recommended]**
- **Window.** From the start of each channel's catch-up window (`catchup_days`) to the end of the imported guide. **[recommended]**
- **Consistency.** The same programmes and times appear in `get_short_epg` and `get_simple_data_table`, whose `title` and `description` are the base64 of the same text and whose `start` and `end` are the same UTC times. **[recommended]**; it is not yet cross-checked.

## Golden sample

`fixtures/xmltv.xml` has one English channel with a logo and four consecutive programmes (the same ones as the EPG fixtures), and one Arabic channel without a logo. The excerpt below shows two of its five programmes:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<tv generator-info-name="Smart IPTV">
  <channel id="showcase.example">
    <display-name lang="en">Showcase 24/7</display-name>
    <icon src="https://media.example.com/images/channel-3001/logo/w185.eccbc87e.webp"/>
  </channel>
  <channel id="family-cinema.example">
    <display-name lang="ar">سينما العائلة</display-name>
  </channel>
  <programme start="20261003153000 +0000" stop="20261003163000 +0000" channel="showcase.example">
    <title lang="en">Inside the Studio</title>
    <desc lang="en">How a title reaches your screen, from the library folder to direct play.</desc>
  </programme>
  <programme start="20261003160000 +0000" stop="20261003180000 +0000" channel="family-cinema.example">
    <title lang="ar">فيلم السهرة</title>
    <desc lang="ar">فيلم عائلي من إنتاج المنصة.</desc>
  </programme>
</tv>
```

The invalid cases `fixtures/invalid/xmltv--*.json` cover:

- the document (`not-well-formed`, `entity-declaration`);
- order and channels (`channel-after-programmes`, `missing-display-name`, `unknown-channel`, `channel-not-in-catalog`);
- times (`riyadh-offset`, `time-with-dashes`, `stop-before-start`);
- titles (`empty-title`).
