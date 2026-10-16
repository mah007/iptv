# ADR-0027: Licensed Xtream VOD imports (XV1)

- **Status:** Accepted
- **Date:** 2026-10-17
- **Milestone:** XV1

## Context

Some operators receive films through an Xtream-compatible VOD service under a separate agreement that permits them to copy and redistribute selected titles. Downloading a stream by hand and then uploading it to this server wastes time and bandwidth, while a stream URL embeds the account username and password.

Subscription access alone commonly grants viewing only. This feature must therefore preserve SPEC §1.1: no discovery or automated acquisition, and no title enters the library without an explicit rights record.

## Decision

- Add `xtream` as an import-source kind. The source stores only its base URL in options. The account username and password are Fernet-encrypted write-only secrets; API responses and audit rows expose only which fields are set.
- Test authenticates through `player_api.php` and lists VOD categories. Browse maps categories and movies to opaque paths. Categories are navigation only and cannot be selected; an admin explicitly selects one or more movies.
- A job persists movie ids and safe names, never a credential-bearing stream URL. At run time the importer resolves the current movie record and constructs its stream URL in memory.
- Reuse the direct downloader's guarded redirect handling, size probe, Range resume, bandwidth throttle, progress, cancellation and disk watchdog, then the importer's staging, atomic move, audit, rights application and library scan.
- Every request is protected by the egress guard. HTTPS sources permit HTTPS redirects only. A source explicitly configured as HTTP may use HTTP or upgrade to HTTPS; the admin gives a prominent plaintext warning.
- Keep the existing required rights holder, licence reference and optional expiry. The application does not infer copying rights from a working account.

## Security and privacy

- `httpx` request logs are disabled below warning and the shared redactor masks Xtream query credentials and `/movie/<user>/<password>/…` paths as defence in depth.
- Connection errors are replaced with stable messages because an `httpx` exception can contain the full request URL.
- Extensions and ids are allow-listed; provider titles become one safe library file name. Redirects are checked before every request.
- A source can be changed or removed only through the existing step-up and permission checks.

## Consequences

- Migration `imports/0002` adds the source choice; it changes no stored row shape and is backward compatible with the rolling deployment.
- The importer can download from legacy HTTP-only panels, but confidentiality depends on the provider adding TLS.
- Whole-category and scheduled synchronization remain out of scope. Series and episodes are also out of scope for XV1.
