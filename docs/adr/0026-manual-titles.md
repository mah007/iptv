# ADR-0026: Manual titles for files no metadata provider knows (MT1)

- **Status:** Accepted
- **Date:** 2026-10-16
- **Milestone:** MT1 ("Manual titles")

## Context
A file TMDB (or TheTVDB) doesn't know stays in the review queue forever: only provider-matched titles became movies. The owner needs to add titles by hand: their own videos, independent productions, regional films TMDB lacks. The platform is for owned or licensed content only (SPEC §1.1), so a manual title carries the same rights fields as every title, and the admin states them.

## Decision
- **Model:** `MetadataSource.MANUAL` (it existed since slice 2, unused). No migration: rights live in the title's existing fields, the confirmation in the audit log, and `MatchReview.chosen_provider` takes `manual` (no id).
- **Creating:** `apps.metadata.services.create_manual_movie` (`POST admin/movies/manual`, multipart or JSON, `library.review`), for an open or skipped review's file or a file no title owns. It follows `accept_movie`:
  - the file becomes `matched` (the media pipeline prepares it, so the title is ready once it plays);
  - the reviews close with `chosen_provider="manual"`;
  - I1's import rights fill what the form left empty;
  - the status follows the files, and `notify_catalog_changed` refreshes search, the Xtream cache and home rows.
- **Required:** title, rights holder, licence reference and the confirmation "I own this content or hold the rights to distribute it". It is audited as `title.create_manual` and `review.manual` with the actor and `rights_confirmed`. An expiry in the past is refused (`not_in_future`).
- **Optional fields:** Arabic title, original title, year, release date, overviews, taglines, certification, original language, countries, categories, genres, trailer and expiry. The runtime defaults to the probed duration, rounded up.
- **Categories:** exactly what the admin ticks. The form starts from the library's defaults, which also apply when the request names none (omitted or empty: multipart can't send an empty list). No genre-mapped categories are added.
- **Form defaults:** `GET admin/movies/manual/defaults?review|media_file` gives the title and year from the file name, the runtime, the import's rights and the library's categories.
- **Locks:** every field a manual movie has is locked except `status` (a status lock means "hidden by an admin").
- **Refresh:** "Refresh metadata" is refused with a new top-level code, `MANUAL_METADATA` (409). The `refresh_title` task leaves a manual movie alone, and automatic rematch of one is refused the same way.
- **Artwork:**
  - Poster and backdrop uploads go to the existing image converter (`media.tasks.store_title_image`, `ImageSource.UPLOAD`): WebP and AVIF, poster w185/w500/original, backdrop w780/w1280. The web checks only the header first (`images.check_image`: format, ≤ 40 MP, ≤ 15 MB).
  - Whatever wasn't uploaded comes from a frame at 10% of the video: the middle 2:3 as the poster and the whole frame as the backdrop (`media.tasks.frame_artwork`, FFmpeg as a process, on the worker's `images` queue).
  - A frame never replaces an image that arrived meanwhile.
- **Reopen:** `POST admin/review-queue/{id}/reopen` puts a skipped review back (audited as `review.reopen`). It is refused when the file was matched since, is being matched, was removed, or already has an open review.
- **Match to TMDB later:** for a manual movie, a rematch with a TMDB id converts the same row in place, so the Xtream id, history and favourites stay.
  - TMDB's metadata replaces the admin's, except the rights, categories, featured flag and visibility.
  - Fields TMDB leaves empty keep the admin's value, locked.
  - If the catalogue already has that TMDB movie, the files move to it, as for any rematch.
  - A TMDB error rolls the change back.
- **Title editor:** the editor gains Details (original title, release date, runtime, certification, language, countries, trailer), genres and Rights for every title. `genres`, `original_language` and `countries` are editable and lockable; `_apply_relations` respects a genres lock.
- **Customer API:** `attribution` returns `manual` for these titles, and the portal shows no provider credit. TMDB's credit stays on the About page.
- **Xtream:** nothing changes. A title without TMDB data already maps to `""` (TMDB and IMDb ids, images, credits) and `[]` (backdrops). A backend contract test and two `compat/fixtures/variants` cases pin it. Variants are a new, additive part of `validate.py`: patches to a golden file that must pass, while the golden files stay canonical.
- **Adult categories:** a manual title follows the same rules as any title.
  - In the portal, search and recommendations (ADR-0013), adult categories show only when the plan names them.
  - In Xtream (ADR-0011, unchanged), a plan with every category lists adult categories too. They are flagged `is_adult:"1"` and the apps lock them themselves.

## Alternatives considered
- **A JSON create followed by the existing per-image uploads:** rejected. The server couldn't tell whether to take the frame fallback, and a half-made title would exist between the requests.
- **A rights-confirmation column on the title:** rejected. The audit log is append-only and already records who confirmed what and when.
- **`CONFLICT` with a field code for the refresh refusal:** rejected. API clients switch on the top-level `code`, and a manual title is a stable condition, not a transient conflict.
- **Manual series:** deferred. Seasons and episodes need their own form and numbering rules.

## Consequences
- Files TMDB will never know become playable titles, with rights stated and audited.
- **Admin:** the review queue shows "Create movie manually" (the main action when there are no candidates) and Reopen. Lists get a Manual badge and a Source filter. A manual title's page has no Refresh, offers "Match to TMDB", and shows uploads and frames in the images panel.
- **Dev:** `scripts/sample_media.sh` adds a sample no provider knows. `e2e_admin_account --manual-review` resets it to a skipped review for the admin journey, deleting the previous run's manual movie (DEBUG only, and only when its single file is that sample).
- **To revisit:** manual series, and bulk creation for many unknown files.
