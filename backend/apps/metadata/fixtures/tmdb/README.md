# Synthetic TMDB fixtures (fixture mode)

**These files are synthetic.** They were written by hand to look like TMDB v3 API
responses so that the platform can match, enrich and illustrate the sample media
(`make sample-media`) offline, with no TMDB key. They are not TMDB data. Every file
carries a `_fixture` note that says so; the fixture transport removes it before serving.

- Real facts: the titles, years, runtimes, genres and the TMDB, IMDb and TVDB IDs of The
  Matrix (603), Inception (27205), Blade Runner 2049 (335984), Wadjda / وجدة (129112) and
  Breaking Bad (1396, seasons 1 and 2), plus a few well-known cast and crew names.
- Made up: overviews, taglines, Arabic translations, ratings, popularity, credit IDs,
  episode and season IDs, and every image path. Image paths end in `-{width}x{height}`;
  the fixture transport answers `image.tmdb.org` requests for them with generated
  placeholder art.
- "Show" (tv/9000001) is an invented series for the multi-episode sample file
  `Show.S02E01E02.mkv`. Its ID is outside the range TMDB uses today.

Layout: `{api path}.json` (for example `movie/603.json`, `tv/1396/season/1.json`),
`{api path}.{language}.json` for a language other than en-US, and `search/movie.json` and
`search/tv.json`, which map a normalised query (`apps.search.normalize`) to a search
response. See `apps/metadata/tmdb/fixtures.py`.

`apps.metadata.tmdb.recording.record_sample_fixtures(...)` replaces these files with real
responses when a key or token is configured. Recorded files are TMDB data under the TMDB
API terms, so keep them out of public repositories.
