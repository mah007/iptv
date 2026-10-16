# Synthetic TheTVDB fixtures (fixture mode)

**These files are synthetic.** They were written by hand to look like TheTVDB v4 API
responses, so the TV fallback (ADR-0023) can be tested offline with no TheTVDB key. They
are not TheTVDB data. Every file carries a `_fixture` note that says so; the fixture
transport removes it before serving.

- "The Lantern Keepers" / «حراس الفانوس» (9100001) and "Lantern Tales" (9100002) are
  invented series. Their IDs are outside the range TheTVDB uses today. 9100001 has two
  seasons in aired order (`official`) and a different DVD order (`dvd`), Arabic names for
  season 1, a poster, a background, a clear logo and episode stills.
- Breaking Bad (81189) carries its real TheTVDB, TMDB (1396) and IMDb IDs; its alias
  "La Química" is made up, so a search that TMDB's fixtures miss finds it on TheTVDB and
  is handed back to TMDB (the cross-walk). Its episode lists in the `official`, `dvd` and
  `absolute` orders (ADR-0024) cover TMDB's two seasons with invented episode ids and
  names; the DVD order is invented too (its season 1 ends with what aired as S02E01).
- Overviews, aliases, artwork paths, ratings and character names are made up. Artwork
  paths end in `-{width}x{height}`; the fixture transport answers `artworks.thetvdb.com`
  requests for them with generated placeholder art.

Layout: `{api path}.json` (for example `series/9100001/extended.json`,
`series/9100001/episodes/official/eng.json`) and `search/series.json`, which maps a
normalised query (`apps.search.normalize`) to a list of search results. See
`apps/metadata/tvdb/fixtures.py`.

`manage.py record_tvdb_fixtures` saves real responses next to these when a TheTVDB key is
configured. Recorded files are TheTVDB data under TheTVDB's licence (attribution, and a
paid tier above USD 50,000 of yearly company revenue), so keep them out of public
repositories.
