import type {
  Candidate,
  Category,
  File,
  Image,
  Library,
  MovieDetail,
  MovieSummary,
  Review,
  ScanJob,
  SeriesDetail,
} from "@smart-iptv/api";
import { act } from "@testing-library/react";
import { vi } from "vitest";

import type { LiveSession } from "../features/sessions/live-sessions";
import { ALL_PERMISSIONS, me, signedIn } from "./fixtures";
import type { Routes } from "./mock-api";

/** An owner who may also run the media pages. */
export const MEDIA_PERMISSIONS = [
  ...ALL_PERMISSIONS,
  "library.view",
  "library.manage",
  "library.review",
  "sessions.kill",
];

/** Signed in with the media permissions, an empty review queue and no categories. */
export function mediaSignedIn(routes: Routes = {}, permissions = MEDIA_PERMISSIONS): Routes {
  return signedIn(
    {
      "GET /api/v1/admin/review-queue": {
        body: { count: 0, next: null, previous: null, results: [] },
      },
      "GET /api/v1/admin/categories": {
        body: { count: 0, next: null, previous: null, results: [] },
      },
      "GET /api/v1/admin/libraries": {
        body: { count: 0, next: null, previous: null, results: [] },
      },
      ...routes,
    },
    me({ permissions }),
  );
}

export function scanJob(overrides: Partial<ScanJob> = {}): ScanJob {
  return {
    id: "scan-1",
    library: { id: "lib-1", name: "Movies" },
    trigger: "manual",
    status: "done",
    path: "",
    found: 5,
    new: 5,
    changed: 0,
    moved: 0,
    removed: 0,
    errors: 0,
    started_at: "2026-10-03T22:00:00Z",
    finished_at: "2026-10-03T22:00:02Z",
    log: "new: The Matrix.mp4",
    created_at: "2026-10-03T22:00:00Z",
    ...overrides,
  };
}

export function library(overrides: Partial<Library> = {}): Library {
  return {
    id: "lib-1",
    name: "Movies",
    kind: "movies",
    path: "/media/movies",
    processing_policy: "ingest",
    default_categories: [],
    scan_interval_min: 15,
    enabled: true,
    last_scan_at: "2026-10-03T22:00:02Z",
    stats: { files: 5, bytes: 14_400_000, movies: 4, review: 1, errors: 0, pending: 0 },
    last_scan: scanJob(),
    created_at: "2026-10-03T21:00:00Z",
    updated_at: "2026-10-03T22:00:02Z",
    ...overrides,
  };
}

export function image(overrides: Partial<Image> = {}): Image {
  const base = "http://media.localhost/images/movie/m1/poster";
  return {
    id: "img-1",
    kind: "poster",
    language: "en",
    url: `${base}/w500.abc.webp`,
    sizes: {
      w185: { webp: `${base}/w185.abc.webp`, avif: `${base}/w185.abc.avif` },
      w500: { webp: `${base}/w500.abc.webp`, avif: `${base}/w500.abc.avif` },
    },
    width: 600,
    height: 900,
    blurhash: "",
    is_primary: true,
    ...overrides,
  };
}

const CATEGORY_BRIEF = { id: "cat-1", kind: "vod", name_en: "Action", name_ar: "أكشن" } as const;

export function movieSummary(overrides: Partial<MovieSummary> = {}): MovieSummary {
  return {
    id: "movie-1",
    xc_id: 10,
    tmdb_id: 603,
    title: "The Matrix",
    title_ar: "المصفوفة",
    original_title: "The Matrix",
    year: 1999,
    status: "ready",
    rating: 8.2,
    popularity: 80.5,
    metadata_source: "synthetic",
    synthetic: true,
    poster: image(),
    categories: [CATEGORY_BRIEF],
    file_count: 2,
    has_arabic_overview: true,
    created_at: "2026-10-03T21:00:00Z",
    updated_at: "2026-10-03T22:00:00Z",
    runtime_min: 136,
    ...overrides,
  };
}

export function mediaFile(overrides: Partial<File> = {}): File {
  return {
    id: "file-1",
    library: { id: "lib-1", name: "Movies" },
    relative_path: "The.Matrix.1999.1080p.BluRay.x265.mkv",
    size: 2_580_118,
    mtime: null,
    container: "mkv",
    duration_ms: 8_160_000,
    bitrate: 688_031,
    video_codec: "hevc",
    video_profile: "Main",
    video_level: 120,
    width: 1920,
    height: 1080,
    fps: 23.976,
    hdr: "sdr",
    audio: [
      {
        stream_index: 1,
        codec: "ac3",
        channels: 6,
        language: "eng",
        title: null,
        default: true,
        forced: false,
      },
    ],
    subtitles: [],
    direct_play: false,
    match_confidence: 1,
    state: "matched",
    error: "",
    version_label: "",
    is_primary: true,
    removed_at: null,
    created_at: "2026-10-03T21:00:00Z",
    ...overrides,
  };
}

const DETAIL_BASE = {
  imdb_id: "tt0133093",
  alt_titles: [],
  overview: "A hacker learns the truth.",
  overview_ar: "مخترق يكتشف الحقيقة.",
  tagline: "Welcome to the real world.",
  tagline_ar: "",
  vote_count: 26000,
  certification: "R15",
  original_language: "en",
  countries: ["US"],
  trailer_youtube_key: "",
  featured: false,
  rights_holder: "",
  license_ref: "",
  license_expires_at: null,
  metadata_locked_fields: [],
  metadata_refreshed_at: "2026-10-03T21:00:00Z",
  genres: [{ id: "g-1", tmdb_id: 28, name_en: "Action", name_ar: "أكشن" }],
  images: [image()],
  credits: [],
} as const;

export function movieDetail(overrides: Partial<MovieDetail> = {}): MovieDetail {
  const {
    poster: _poster,
    file_count: _count,
    has_arabic_overview: _ar,
    ...summary
  } = movieSummary();
  return {
    ...summary,
    ...DETAIL_BASE,
    release_date: "1999-03-31",
    files: [mediaFile()],
    ...overrides,
  };
}

export function seriesDetail(overrides: Partial<SeriesDetail> = {}): SeriesDetail {
  return {
    id: "series-1",
    xc_id: 1,
    tmdb_id: 1396,
    title: "Breaking Bad",
    title_ar: "بريكنج باد",
    original_title: "Breaking Bad",
    year: 2008,
    rating: 8.9,
    popularity: 90,
    status: "ready",
    metadata_source: "synthetic",
    synthetic: true,
    categories: [],
    created_at: "2026-10-03T21:00:00Z",
    updated_at: "2026-10-03T22:00:00Z",
    ...DETAIL_BASE,
    tvdb_id: null,
    first_air_date: "2008-01-20",
    last_air_date: null,
    episode_run_time: 47,
    episode_ordering: "tmdb_default",
    seasons: [
      {
        id: "season-1",
        number: 1,
        name: "Season 1",
        name_ar: "",
        overview: "",
        overview_ar: "",
        air_date: null,
        episode_count: 2,
        poster: null,
        episodes: [
          {
            id: "ep-1",
            xc_id: 100,
            number: 1,
            absolute_number: null,
            title: "Pilot",
            title_ar: "",
            overview: "",
            overview_ar: "",
            air_date: null,
            runtime_min: 58,
            rating: null,
            still: null,
            files: [
              mediaFile({
                id: "file-ep1",
                library: { id: "lib-2", name: "Series" },
                relative_path: "Breaking Bad/Season 01/Breaking.Bad.S01E01.720p.mkv",
              }),
            ],
          },
          {
            id: "ep-2",
            xc_id: 101,
            number: 2,
            absolute_number: null,
            title: "Cat's in the Bag...",
            title_ar: "",
            overview: "",
            overview_ar: "",
            air_date: null,
            runtime_min: 48,
            rating: null,
            still: null,
            files: [],
          },
        ],
      },
    ],
    ...overrides,
  };
}

export function candidate(overrides: Partial<Candidate> = {}): Candidate {
  return {
    provider: "tmdb",
    kind: "movie",
    id: 603,
    title: "The Matrix",
    original_title: "The Matrix",
    year: 1999,
    runtime_min: 136,
    popularity: 80.5,
    overview: "A hacker learns the truth.",
    poster_url: null,
    score: 0.91,
    breakdown: { title: 1, year: null, runtime: null, popularity: 1, matched_title: "The Matrix" },
    ...overrides,
  };
}

export function review(overrides: Partial<Review> = {}): Review {
  return {
    id: "review-1",
    kind: "movie",
    reason: "ambiguous",
    status: "open",
    media_file: {
      id: "file-1",
      library: { id: "lib-1", name: "Movies" },
      relative_path: "The Matrix.mp4",
      size: 3_409_111,
      container: "mp4",
      duration_ms: 30_000,
      video_codec: "h264",
      width: 1280,
      height: 720,
      hdr: "sdr",
      state: "review",
    },
    parse_result: null,
    candidates: [
      candidate(),
      candidate({ id: 624860, title: "The Matrix Resurrections", year: 2021, score: 0.88 }),
    ],
    chosen_provider_id: null,
    decided_by: "",
    decided_at: null,
    created_at: "2026-10-03T22:00:00Z",
    ...overrides,
  };
}

export function category(overrides: Partial<Category> & Pick<Category, "id">): Category {
  return {
    xc_id: 1,
    kind: "vod",
    name_en: "Action",
    name_ar: "أكشن",
    slug: overrides.id,
    sort: 0,
    is_adult: false,
    visible_in_xtream: true,
    icon: "",
    parent: null,
    ...overrides,
  };
}

export function liveSession(overrides: Partial<LiveSession> = {}): LiveSession {
  return {
    id: "sess-1",
    user: { id: "cust-1", name: "Sara Ahmed" },
    device: { id: "dev-1", name: "Living room TV" },
    title: { kind: "movie", id: "movie-1", name: "The Matrix" },
    rendition: "compat",
    ip: "198.51.100.7",
    country: "SA",
    edge: "edge-1",
    started_at: new Date(Date.now() - 65_000).toISOString(),
    last_seen_at: new Date().toISOString(),
    bytes_sent: 52_000_000,
    ...overrides,
  };
}

/** A stand-in for the browser's EventSource that a test drives by hand. */
export class FakeEventSource {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 2;
  static instances: FakeEventSource[] = [];

  readonly url: string;
  readyState = FakeEventSource.CONNECTING;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  private readonly listeners = new Map<string, Set<(event: MessageEvent) => void>>();

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }

  addEventListener(name: string, listener: (event: MessageEvent) => void): void {
    const set = this.listeners.get(name) ?? new Set();
    set.add(listener);
    this.listeners.set(name, set);
  }

  close(): void {
    this.readyState = FakeEventSource.CLOSED;
  }

  /** The server answered: the stream is open. */
  open(): void {
    act(() => {
      this.readyState = FakeEventSource.OPEN;
      this.onopen?.();
    });
  }

  /** The server sent `event: name` with `data` as JSON. */
  emit(name: string, data: unknown): void {
    act(() => {
      for (const listener of this.listeners.get(name) ?? []) {
        listener(new MessageEvent(name, { data: JSON.stringify(data) }));
      }
    });
  }

  /** The streams opened for `url` that are still open. */
  static open(url: string): FakeEventSource[] {
    return FakeEventSource.instances.filter(
      (source) => source.url === url && source.readyState !== FakeEventSource.CLOSED,
    );
  }
}

export function installEventSource(): typeof FakeEventSource {
  FakeEventSource.instances = [];
  vi.stubGlobal("EventSource", FakeEventSource);
  return FakeEventSource;
}
