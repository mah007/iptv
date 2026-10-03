# Parser test data

Ground truth for the library parser (M4) and the input side of the metadata matcher (M5): SPEC §7.1, §7.2 and §15.

| File | Purpose |
|---|---|
| `filenames.csv` | Release-style relative paths, each with the parse a careful person reads from it (at least 320 rows; SPEC §15 asks for 300). |
| `check_filenames.py` | Validates the CSV. Run it after every edit: `python3 backend/tests/data/check_filenames.py`. |

The CSV holds names only, no media. Playable files come from `scripts/sample_media.sh` (see [Sample media](#sample-media)).

## File format

- UTF-8 without a BOM, LF line ends, one header row, RFC 4180 quoting (a value with a comma or a quote is double-quoted).
- Read it with `csv.DictReader(handle)` from a handle opened with `encoding="utf-8", newline=""`, and address columns by name. New columns may be added later; existing ones are never renamed or reordered.
- An empty cell means the path does not say. Never fill a cell from outside knowledge, such as a year you happen to know.

## Columns

| Column | Meaning | Format |
|---|---|---|
| `path` | Path relative to the library root: folders and filename, `/` separators, exactly as on disk | Text; unique, ignoring case and Unicode normalisation |
| `kind` | `movie`, `episode` or `ignore` | One of the three |
| `title` | The movie title; for an episode, the series title (never the episode title) | Text |
| `year` | Release year of a movie, or the year that tells a series apart (`Doctor Who (2005)`) | 4 digits |
| `season` | Season number; `0` for specials | Integer, no padding (`S01` → `1`) |
| `episodes` | Episode numbers within the season | Ascending integers joined by `;` (`1`, `1;2`) |
| `absolute_episode` | Episode number counted across the whole series | Integer |
| `air_date` | Air date of a dated (daily) episode | `YYYY-MM-DD` |
| `edition` | Edition labels of a movie | Labels joined by `;` |
| `tmdb_id_hint` | TMDB id from a `[tmdbid-603]`, `[tmdbid=603]` or `{tmdb-603}` tag | Integer |
| `imdb_id_hint` | IMDb id from an `[imdbid-tt0133093]` or `{imdb-tt0133093}` tag | `tt` + 7 or 8 digits |
| `tvdb_id_hint` | TVDB id from a `[tvdbid-81189]` or `{tvdb-81189}` tag | Integer |
| `notes` | Why the row exists: the trap it sets for a parser | Free text |

Rows marked `ignore` leave every column from `title` to `tvdb_id_hint` empty, and their notes say why the file is skipped.

`air_date`, `imdb_id_hint` and `tvdb_id_hint` are in addition to the columns the M4 plan named. A dated episode has no season or episode number to assert, and an IMDb or TVDB id cannot go in `tmdb_id_hint`.

## How the expected values are read

### Kind

- Decided from the path alone, as a person would decide it. In a scan the library kind (movies or series) is known as well (SPEC §7.2 step 2) and must agree; every row here is decidable without it.
- `ignore` covers everything the scanner must not turn into a title:
  - extensions SPEC §7.1 does not accept (accepted, in any case: `mkv mp4 m4v mov avi ts m2ts webm`);
  - samples, trailers and extras: folders such as `Sample`, `Trailers`, `Extras`, `Featurettes`, `Behind The Scenes`, `Deleted Scenes`, `Interviews`, `Shorts`, `Scenes`; suffixes `-sample`, `-trailer`, `-featurette`, `-behindthescenes`, `-deleted`, `-interview`, `-short`; a standalone `SAMPLE` or `Trailer` token;
  - sidecars (subtitles, `.nfo`, artwork, chapter files);
  - hidden files and system folders (`@eaDir`, `#recycle`, `$RECYCLE.BIN`, `.Trash-1000`, `lost+found`).
- A word on its own does not make a file an extra. `Free.Samples.2012`, `Trailer Park Boys` and `Extras (2005)/Season 01/…` are titles.

### Title

- Separators (`.`, `_`, spaces, `-` between words, repeats) read as single spaces. Hyphens inside a word stay (`Spider-Man`, `WALL-E`, `9-1-1`), and so does a spaced hyphen inside a title (`Mission Impossible - Fallout`).
- Removed from the title:
  - the year and all release tags (resolution, source, codec, audio, HDR, language, `PROPER`, `REPACK`, release group, CRC);
  - season and episode markers and the episode title;
  - the edition and provider-id tags;
  - a country disambiguator (`The Office (US)` → `The Office`);
  - the Arabic prefixes `فيلم` (film) and `مسلسل` (series).
- Kept in the title:
  - articles (`The`, `La`, `Das`, `Al-`, `El`);
  - numbers that belong to it (`1917`, `300`, `Apollo 13`, `Toy Story 4`, `The 100`, `Mob Psycho 100`, `Planet Earth II`);
  - `Part Two`, `Chapter 4`, and `Episode IV` in a movie title.
- `Matrix, The` reads as `The Matrix`.
- Arabic stays exactly as written. Alef, yaa and taa marbuta are not folded and tashkeel is not stripped: that is the matcher's normalisation (SPEC §7.2 step 4), not the parser's. Arabic-Indic digits stay part of the title (`الفيل الأزرق ٢`).
- Capitalisation follows normal writing (`BRAVEHEART` → `Braveheart`). Case is not significant.

Compare titles in tests after this normalisation, so that punctuation differences pass but a missing or extra word fails:

```python
import re
import unicodedata

ARABIC_INDIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


def comparable_title(title: str) -> str:
    """NFC, case-folded, Arabic-Indic digits as ASCII, words joined by single spaces."""
    text = unicodedata.normalize("NFC", title).casefold().translate(ARABIC_INDIC_DIGITS)
    return " ".join(re.findall(r"[^\W_]+", text))
```

For example, `E.T. the Extra-Terrestrial` and `E T the Extra Terrestrial` compare equal.

### Year

- Taken only from the path: the folder (`The Prestige (2006)/The Prestige.mkv`) or the filename.
- A year-like number at the start belongs to the title, and the later year is the year (`2001.A.Space.Odyssey.1968`, `1917.2019`, `2012.2009`). A number-only series title is never a year (`1883.S01E01.1883`).
- For an episode it is the series year (`Doctor Who (2005)`, `Shogun.2024.S01E01`). The year inside an air date is not a series year.
- Arabic-Indic digits are converted: `(٢٠١٩)` → `2019`.

### Season and episodes

- Markers read: `S01E01`, `s1e1`, `S02.E04`, `S02 E05`, `1x02`, `Season 2 Episode 6`, `E02` in an `S02` folder, `Episode 03`, a bare `02 - Title` in a season folder.
- Season folders read: `Season 01`, `Season 1`, `Series 2`, `S02`, `Season 0`, `Season 00`, `Specials`. In Arabic: `الموسم 01` (season), `الجزء 2` (part, used as the season) and `الحلقة 05` (episode).
- A marker in the filename wins over a contradicting season folder (`The Wire/Season 2/The.Wire.S03E01…` is S3E1).
- Multi-episode files list every episode: `S10E17E18`, `s02e12-e13`, `1x16-1x17`, `S01E01-02`. A range expands: `S02E01-E03` → `1;2;3`.
- Specials (`S00E01`, or a `Specials` folder) have season `0`. A special without a number has season `0` and no episodes.
- `absolute_episode` is used when the number is not season-relative:
  - anime ` - 105 `;
  - `E1088` with no season;
  - `Show - 001 - Title`;
  - `الحلقة 3` with no season.

  With a season in the name (`Attack on Titan S2 - 03`), the number is season-relative and goes in `episodes`. A `v2` suffix marks a re-release and is not part of the number.
- `air_date` comes from `2024.03.15`, `2024-03-15`, `20240315` or `15.03.2024` (day first, because 15 cannot be a month). Dated episodes have no season or episodes.

### Edition

- The canonical labels are:
  - `Director's Cut`, `Extended`, `Theatrical`, `Unrated`, `Uncut`, `Remastered`, `IMAX`;
  - `Final Cut`, `Special Edition`, `Ultimate Edition`, `Ultimate Cut`, `Collector's Edition`, `Criterion`.
- Spellings map to these labels:
  - `Directors.Cut` and `DIRECTORS.CUT` → `Director's Cut`;
  - `Extended.Edition` → `Extended`;
  - `Theatrical.Cut` → `Theatrical`;
  - `The.Final.Cut` → `Final Cut`;
  - `COLLECTORS.EDITION` → `Collector's Edition`.
- Several labels are joined with `;` in the order written: `EXTENDED.REMASTERED` → `Extended;Remastered`, `Extended.Directors.Cut` → `Extended;Director's Cut`.
- An explicit Plex tag is taken verbatim: `{edition-The Richard Donner Cut}` → `The Richard Donner Cut`. A Jellyfin suffix ` - Director's Cut` is an edition, but ` - 1080p` is only a version of the same movie.
- Only movies have editions.

### Provider ids

- Tags are read from the folder or the filename: `[tmdbid-603]`, `[tmdbid=603]` (Emby), `{tmdb-603}`, `[imdbid-tt0133093]`, `{imdb-tt0133093}`, `[tvdbid-81189]`, `{tvdb-81189}`.
- An id makes the match certain (confidence 1.0, SPEC §7.2 step 4).
- An empty tag (`[tmdbid-]`) gives no hint and must not end up in the title.
- A row never carries two different ids for the same provider; the checker rejects that.

### Unicode

- Every value is NFC, except one deliberate `path`: `Léon (1994)/…` is stored decomposed (NFD), as macOS and some SMB servers write names, and its notes say so.
- Normalise paths to NFC before parsing or comparing them.

## Using it in a test

```python
import csv

import pytest
from django.conf import settings

with (settings.BASE_DIR / "tests" / "data" / "filenames.csv").open(
    encoding="utf-8", newline=""
) as handle:
    ROWS = list(csv.DictReader(handle))


@pytest.mark.parametrize("row", ROWS, ids=lambda row: row["path"])
def test_parse(row: dict[str, str]) -> None: ...
```

`settings.BASE_DIR` is the `backend/` folder.

## Checking the file

`check_filenames.py` uses only the standard library and exits non-zero on any problem. It checks:

- the header and the field count;
- at least 320 rows;
- unique paths, ignoring case and Unicode normalisation;
- valid kinds and value formats;
- `ignore` rows: no expected values, and a note;
- that each expected value can be read from its path (title words, year, `SxxEyy` and `NxMM` markers, episode numbers, air dates, editions, provider ids).

It also prints coverage counts: Arabic-script names, multi-episode files, specials, absolute numbering, dated episodes, editions, provider ids, and years found only in a folder name.

It cannot judge whether an expectation is the right reading. That stays with the person adding the row.

To add rows:

1. Write a realistic name.
2. Fill in only what the path states.
3. Say in `notes` what the row tests.
4. Run the checker.

## Sample media

`scripts/sample_media.sh [TARGET_DIR]` (default `./media`) generates legal synthetic media with FFmpeg: `smptehdbars` and `testsrc2` video with sine-tone audio. The clips run 30 to 60 s, and the whole set is about 20 MB. The script:

- keeps files that already exist;
- writes each file in a hidden work folder and renames it into place when complete;
- uses the `ffmpeg` on `PATH`, or a pinned FFmpeg container image when there is none.

| File (under `TARGET_DIR`) | What it exercises |
|---|---|
| `movies/The.Matrix.1999.1080p.BluRay.x265.mkv` | HEVC Main 1080p, AC-3 5.1, embedded English subtitles |
| `movies/Blade.Runner.2049.2017.2160p.UHD.BluRay.x265.10bit.HDR.mkv` | HEVC Main 10 2160p, HDR10 (BT.2020, SMPTE 2084, mastering display and light level), E-AC-3 5.1 |
| `movies/Inception (2010)/Inception.2010.1080p.BluRay.DTS.x264.mkv` | H.264 1080p, DTS 5.1 |
| `movies/Inception (2010)/Inception.2010.1080p.BluRay.DTS.x264.ar.srt` | Arabic subtitles in Windows-1256 (cp1256), CRLF, no BOM |
| `movies/وجدة (2012)/وجدة.2012.1080p.WEB-DL.mkv` | Arabic-script title, H.264 1080p, AAC stereo (ara) |
| `series/Breaking Bad/Season 01/Breaking.Bad.S01E01.720p.mkv` | H.264 720p, AAC: direct-play compatible |
| `series/Breaking Bad/Season 01/Breaking.Bad.1x02.mkv` | `NxMM` marker, H.264 480p, MP3, no colour tags |
| `series/Show/Season 02/Show.S02E01E02.mkv` | Multi-episode file, 60 s, English and Arabic AAC tracks, one chapter per episode |

These files also appear in `filenames.csv`, relative to their library root (without `movies/` or `series/`), with notes starting `sample_media.sh`.
