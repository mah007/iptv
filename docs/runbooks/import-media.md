# Runbook: import media from storage, licensed VOD or a direct link

Imports copy files you own, or are licensed to distribute, into a library. They come from storage you control (an S3-compatible bucket, an SFTP or WebDAV server, Google Drive, Dropbox or OneDrive), a licensed Xtream-compatible VOD account, or one direct HTTPS link such as a public-domain film on the Internet Archive. The server downloads them itself, at its own connection's speed, so a 100 GB film never crosses your home uplink. The decisions are in [ADR-0025](../adr/0025-imports-from-storage-and-direct-links.md) and [ADR-0027](../adr/0027-licensed-xtream-vod-imports.md).

**What it is not.** There are no torrents or magnet links, no Usenet, no indexers, no search-and-download, no RSS and no automatic grabbing. A subscription that permits viewing does not by itself permit copying or redistribution. Every import is started by an admin, for files or individual VOD movies they chose or one link they pasted. It records:
- who started it;
- what it brought, and from where;
- under which rights: a rights holder and a licence reference are required. For a public-domain film, write *Public domain* and the page that shows it.

## What runs
| Service | Does |
|---|---|
| `importer` | Runs the imports: rclone (MIT, a separate process) for sources, our resumable downloader for links. It consumes only the `import` queue and is the only service that may write the media folder. Everything else keeps it read-only. |
| `worker`, `watcher` | Scan, match and transcode what arrives, as for any file in a library. |

`install.sh --update` builds and starts the `importer` and applies the migration. It also gives the importer's user (uid 10001) write access to `MEDIA_ROOT` with an ACL (`setfacl -R -m u:10001:rwX,d:u:10001:rwX`): owners stay as they are, and folders you add later are covered too. On a file system without ACLs (some NFS or SMB shares), the installer warns you. Make the media folder writable for uid 10001 yourself, for example with `chown -R 10001 /srv/media/movies`.

## Settings (admin → Settings → Library)
| Setting | Default | Meaning |
|---|---|---|
| Imports at once | 1 | How many imports copy at the same time (1 or 2); the others wait in the queue |
| Free space kept by imports | 10 GB | An import that would leave less than this free on the library's disk is refused before it copies anything |
| Import bandwidth | 0 (no limit) | The most an import downloads per second, in Mbit/s |

## Sizing
- **Free space:** an import needs the files' size plus the reserve free on the library's disk, counting other imports running to the same disk. The admin shows each library's free space when you choose it.
- **Time:** at 1 Gbit/s (about 110 MB/s in practice) a 100 GB film takes about 15 minutes; at 100 Mbit/s, about 2½ hours. The source's own limits often decide: B2 and R2 are fast, while Google Drive's shared rclone client is throttled (use your own OAuth client for large libraries; see below).
- **After the copy:** transcoding writes the renditions to the media volume: an MP4 roughly the size of a 1080p source's video, plus the HLS ladder. Where the media volume and the libraries share a disk, raise the reserve so the renditions fit too.

## Import from an S3-compatible bucket (AWS, Backblaze B2, Cloudflare R2, Wasabi, MinIO)
Create a **read-only** key for the bucket at the provider. The importer only lists and reads.

1. Admin → **Libraries** → **Import** → **Add source**. Kind **S3-compatible bucket**; then the steps for your provider:

   | Provider | Endpoint | Region | Path style |
   |---|---|---|---|
   | Amazon S3 | none | the bucket's region | off |
   | Backblaze B2 | `https://s3.<region>.backblazeb2.com`, from the bucket's details | the bucket's region | off |
   | Cloudflare R2 | `https://<account id>.r2.cloudflarestorage.com` | `auto` or empty | off |
   | Wasabi | `https://s3.<region>.wasabisys.com` | the region | off |
   | MinIO | its address, e.g. `http://nas.lan:9000` | empty | on |

2. Paste the access key ID and the secret access key, and set the start folder to `bucket` or `bucket/folder`. Saving asks you to confirm it's you.
3. Click **Test**. *Reachable* means the importer listed the folder. Otherwise the card says why: sign-in refused, not found, unreachable, timed out, certificate not trusted, or *not allowed* (addresses inside the server are always refused).

## Import from an SFTP server
1. **Add source**, kind **SFTP**: host, port (22) and user name, then a password or a private key (PEM or OpenSSH, with its passphrase if it has one).
2. **Host key:** paste the server's public key (`ssh-keyscan -t ed25519 your.server` prints it). Or leave it empty: the first successful **Test** pins the key it sees, and the card shows it to compare. After that, a different key is refused. To accept a legitimate new key, clear the field and Test again.
3. rclone verifies checksums over SFTP when the server lets it run `md5sum` or `sha1sum`. An SFTP-only account (`internal-sftp`) gets size checks only.

## Import from WebDAV (Nextcloud, ownCloud, SharePoint)
**Add source**, kind **WebDAV**: the WebDAV address (for Nextcloud, `https://cloud.example.com/remote.php/dav/files/<user>/`), the server software, a user and a password. With Nextcloud, use an app password.

## Import from Google Drive, Dropbox or OneDrive (through rclone)
The sign-in happens on **your own computer**, so your password never reaches the server:
1. Install rclone on your computer (<https://rclone.org/downloads/>).
2. Run, for the service you use:
   ```
   rclone authorize "drive"
   rclone authorize "dropbox"
   rclone authorize "onedrive"
   ```
   A browser opens. Sign in and allow access. rclone then prints:
   ```
   Paste the following into your remote machine --->
   {"access_token":"…","token_type":"Bearer","refresh_token":"…","expiry":"…"}
   <---End paste
   ```
3. **Add source** with that kind, and paste the JSON (or the whole block) into **Token**. Save, then **Test**.
4. **OneDrive:** the first Test finds your drive (its ID and type) from the token, with rclone's own configuration flow. If it can't (for example the token is older than an hour and couldn't be refreshed), run `rclone config` on your computer to create the OneDrive remote. Then `rclone config show <name>` shows `drive_id` and `drive_type`: paste them in the source's fields.
5. **Google Drive:** set **Folder ID** (the last part of a folder's address) to start in one folder. For large libraries, create your own OAuth client in the Google Cloud console and run `rclone authorize "drive" "<client id>" "<client secret>"`. Then fill **Client ID** and **Client secret**, which must be the ones the token was made with.

The token refreshes itself; when the provider rotates it (OneDrive does), the server keeps the new one, encrypted. Revoke access at the provider when you delete the source.

## Import from a licensed Xtream / IPTV VOD account

Use this only when the rights holder or your contract separately permits copying and redistribution; ordinary subscriber access is not enough.

1. **Add source**, kind **Xtream / IPTV VOD**. Enter the server address only (for example `https://provider.example:8080`), the account username and password. Both account fields are encrypted and never shown again.
2. Prefer HTTPS. If the provider offers only `http://`, the admin warns you: the credentials and movie bytes are not encrypted in transit. An HTTPS source is never allowed to redirect down to HTTP.
3. Click **Test**, then **Browse**. Open a category and tick individual movies. A category cannot be ticked as a whole, so the importer never becomes an automatic catalog grabber.
4. Choose the library and enter the independent rights holder and licence reference. The server probes each movie, checks disk space, downloads it resumably, and runs the normal scan, match and transcode pipeline.

The account credentials exist only in the encrypted source row and importer memory. Jobs and import history keep the source name, opaque movie id and safe file name—not a playback URL containing the account.

## Choose files and start
1. On a source, click **Browse**. Folders come first, then files with their sizes. Only folders, video files (`mkv`, `mp4`, `m4v`, `mov`, `avi`, `ts`, `m2ts`, `webm`) and subtitles (`srt`, `vtt`, `ass`, `ssa`) can be chosen. A folder brings its videos and subtitles only, and leaves posters, `.nfo` files and the like behind.
2. Tick what you want, then **Next**.
3. Choose the **library** and, optionally, a **folder inside it** (e.g. `Classics`). The folders you chose keep their names inside it, so `Films/Metropolis (1927)/` arrives as `Classics/Metropolis (1927)/`. Name films and episodes as for any library (*Title (Year)*, *Show S01E02*).
4. Fill in the **rights holder**, the **licence reference** and, if the licence ends, its **end date**. They are recorded with the import and given to the titles its files are matched to, in the fields those titles don't have yet. A title edited by an admin keeps its own values. Titles whose licence ended are hidden.
5. **Start import.** The **Queue** shows each import: listing and checking, then copying (bytes, speed, time left), then moving into the library. The history keeps who imported what, from where, under which rights, and the files it placed.

What the importer does:
- **Before copying:** it refuses names already in the library, other file types you chose explicitly, and copies that would break the reserve. Nothing is copied first.
- **Staging:** it copies into a hidden folder inside the library (`.smart-iptv-import/`), which the scanner never reads. It then moves each finished file into place in one step: subtitles first, never overwriting a file.
- **Scanning:** it queues a scan of what arrived. Matching and transcoding follow as for any file. Doubtful matches go to the review queue.
- **Failures:** an error that may pass (the source unreachable, a timeout, a dropped connection) is retried once after a minute, resuming from what was copied. Any other failure stops the import and deletes what was staged.
- **Cancel:** a queued import ends at once. A running one stops within seconds, and what was copied so far is deleted. Files already moved stay in the library.

## Import one direct link
1. **Direct link** at the top of the Import page.
2. Paste an `https://` link. For the Internet Archive, use the file's download link: `https://archive.org/download/<item>/<file>.mp4`.
3. The **file name** is optional; otherwise it comes from the link or from the server's `Content-Disposition`. It must be a video or subtitle type.
4. The **checksum** is optional. The Internet Archive lists each file's MD5 and SHA-1 in the item's `<item>_files.xml`. Paste one and the importer verifies it after the download.
5. Choose the library, folder and rights as above, then **Start import**.

The download goes through the same guard as every address an admin types: no addresses inside the server, no redirect to plain HTTP. A dropped connection resumes with an HTTP range request from where it stopped, up to six attempts. A server that doesn't support ranges makes it start over.

## The rsync alternative
On a server you can reach over SSH, rsync works without the admin. For example, from the source machine:
```
rsync -a --partial-dir=.rsync-partial --delay-updates --info=progress2 \
  "/films/Metropolis (1927)" root@your-server:/srv/media/movies/
```
- `--partial-dir` and `--delay-updates` keep half-copied files under hidden names until the end. The watcher also waits until a file's size has been still for 60 s (Settings → Library).
- Files copied this way carry **no rights**: set the rights holder and licence on the titles in the admin afterwards, and keep your own record of where they came from.
- The importer is the better choice when the files are in a bucket or a cloud drive, when you want the rights recorded with the copy, or when the server can't be reached over SSH from the source.

## Titles TMDB doesn't know (manual titles, ADR-0026)

Your own videos and films TMDB lacks wait in **Library → Review queue** with *No candidates*.

1. Open the file and choose **Create movie manually**. The form starts from the file name's title and year, the file's runtime, the rights you gave the import, and the library's categories.
2. Fill in the English and Arabic texts. Tick categories: without one, IPTV apps don't list the title.
3. Upload a poster (2:3) and a backdrop (16:9), or leave them out: a frame of the video is used, and you can replace it on the title page.
4. Enter the rights holder and licence reference, tick **I own this content or hold the rights to distribute it** (recorded in the audit log with your name), and choose **Create movie**. The title is ready once its file is prepared.

A skipped review comes back with **Reopen** (Skipped tab). A manual title is never refreshed. **Match to TMDB** on its page converts it in place if TMDB adds it later; the Xtream id, rights and categories stay.

**Adult categories:** the portal shows them only to plans that name them. IPTV apps get every category of a plan that allows all categories, adult ones flagged for the app's own parental lock (ADR-0011). Give adult titles their own category and leave it out of the plans that shouldn't have it.

## Troubleshooting
| The card or history says | What to do |
|---|---|
| The importer isn't running | `docker compose ... ps importer`, then `logs importer`. `install.sh --update` starts it. |
| Sign-in refused | Check the key or password, its permissions on the bucket or folder, and for OAuth sources that the token wasn't revoked (paste a new one). For SFTP, a changed host key also shows as refused: clear the host key and Test again if the change was expected. |
| Not found | The start folder, bucket or path doesn't exist (names are case-sensitive). |
| Unreachable, timed out | DNS, the firewall, or the provider is down. The importer is on the `egress` network; check the server's outbound rules. |
| Certificate not trusted | The server's TLS certificate doesn't verify. Fix the certificate; the importer doesn't skip verification. |
| Not allowed | The address is the server itself, the local network's link-local or metadata addresses, or one of the stack's own services. These are never fetched from. |
| Not enough free space | Free space on the library's disk, or lower **Free space kept by imports**. |
| Already in the library | Rename or remove the existing file, or import into another folder. |
| The importer stopped during the import | A restart while copying. The sweeper requeues the import once to resume. |
| Permission denied in the importer's log | The media folder isn't writable for uid 10001: run `install.sh --update` (the ACL), or fix the permissions. |
