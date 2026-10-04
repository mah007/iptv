import type {
  Artwork,
  CustomerMe,
  Home,
  MovieDetail,
  MyDevice,
  PlaybackGrant,
  PublicPlan,
  TitleCard,
} from "@smart-iptv/api-portal";

export function artwork(name: string): Artwork {
  const base = `http://media.localhost/images/${name}`;
  return {
    url: `${base}/w500.webp`,
    sizes: {
      w185: { webp: `${base}/w185.webp`, avif: `${base}/w185.avif` },
      w500: { webp: `${base}/w500.webp`, avif: `${base}/w500.avif` },
      w780: { webp: `${base}/w780.webp`, avif: `${base}/w780.avif` },
      w1280: { webp: `${base}/w1280.webp`, avif: `${base}/w1280.avif` },
    },
    width: 500,
    height: 750,
    blurhash: "LEHV6nWB2yk8pyo0adR*.7kCMdnj",
  };
}

export const ACCESS: NonNullable<CustomerMe["access"]> = {
  status: "active",
  expires_at: null,
  max_streams: 2,
  max_devices: 2,
  max_quality: 1080,
  allow_movies: true,
  allow_series: true,
  allow_live: true,
};

export const ME: CustomerMe = {
  id: "user-1",
  username: "layla",
  name: "Layla Hassan",
  email: "layla@example.com",
  phone: "",
  locale: "en",
  timezone: "Asia/Riyadh",
  marketing_opt_in: false,
  status: "active",
  access: ACCESS,
};

/** The first of a list a test expects to be non-empty. */
export function first<T>(items: readonly T[]): T {
  const item = items[0];
  if (item === undefined) throw new Error("Expected at least one item");
  return item;
}

export function card(overrides: Partial<TitleCard> & Pick<TitleCard, "id" | "title">): TitleCard {
  return {
    type: "movie",
    original_title: overrides.title,
    year: 1999,
    rating: 8.2,
    certification: "R15",
    runtime_min: 136,
    poster: artwork(`${overrides.id}/poster`),
    backdrop: artwork(`${overrides.id}/backdrop`),
    ...overrides,
  };
}

export const MATRIX = card({ id: "matrix", title: "The Matrix" });
export const INCEPTION = card({ id: "inception", title: "Inception", year: 2010 });
export const SHOW = card({ id: "show", title: "Breaking Bad", type: "series", year: 2008 });

export const HOME: Home = {
  hero: [
    {
      ...MATRIX,
      overview: "A hacker learns the truth about his reality.",
      tagline: "Welcome to the real world.",
      logo: null,
    },
  ],
  continue_watching: [
    {
      id: "progress-1",
      type: "movie",
      title: INCEPTION,
      episode: null,
      position_ms: 1_800_000,
      duration_ms: 8_880_000,
      completed: false,
      updated_at: "2026-10-04T10:00:00Z",
    },
  ],
  rows: [
    {
      kind: "recently_added",
      key: "recently_added",
      title: "Recently added",
      items: [MATRIX, SHOW],
    },
    {
      kind: "collection",
      key: "collection:classics",
      title: "Classics",
      items: [INCEPTION],
      collection_slug: "classics",
    },
  ],
};

export const MATRIX_DETAIL: MovieDetail = {
  ...MATRIX,
  overview: "A hacker learns the truth about his reality.",
  tagline: "Welcome to the real world.",
  vote_count: 26000,
  original_language: "en",
  countries: ["US"],
  trailer_youtube_key: "vKQi3bBA1y8",
  logo: null,
  genres: [{ id: "g1", name: "Action" }],
  categories: [],
  directors: [{ id: "p1", name: "Lana Wachowski", profile: null }],
  writers: [],
  cast: [{ id: "p2", name: "Keanu Reeves", profile: null, character: "Neo" }],
  quality: { height: 1080, badge: "FHD" },
  tracks: { audio: ["eng"], subtitles: ["ara"] },
  viewer: { favorite: false, rating: null as unknown as "up", progress: null },
  similar: [INCEPTION],
  release_date: "1999-03-31",
};

export const GRANT: PlaybackGrant = {
  session_id: "session-1",
  title_type: "movie",
  title_id: "matrix",
  episode: null,
  delivery: "segmented",
  url: "http://media.localhost/v/token/hls/master.m3u8",
  hls_master_url: "http://media.localhost/v/token/hls/master.m3u8",
  compat_url: null,
  height: 1080,
  expires_at: "2026-10-04T14:00:00Z",
  duration_ms: 30_000,
  resume_ms: 0,
  audio: [{ language: "eng", codec: "aac", channels: 2, default: true }],
  subtitles: [],
  thumbnails_url: null,
};

export function device(overrides: Partial<MyDevice> & Pick<MyDevice, "id" | "name">): MyDevice {
  return {
    kind: "xtream",
    app_hint: "smarters",
    status: "active",
    xtream_username: "living-room",
    first_seen: null,
    last_seen: null,
    last_country: "",
    created_at: "2026-10-01T10:00:00Z",
    current: false,
    ...overrides,
  };
}

export const PLAN: PublicPlan = {
  id: "plan-1",
  code: "monthly",
  name_en: "Monthly",
  name_ar: "شهري",
  description_en: "Everything, every month.",
  description_ar: "كل شيء، كل شهر.",
  duration_months: 1,
  duration_days: 0,
  currency: "SAR",
  price: {
    net: 2522,
    vat: 378,
    total: 2900,
    vat_rate: "0.15",
    display_en: "29.00 SAR",
    display_ar: "29.00 ر.س",
  },
  max_streams: 2,
  max_devices: 2,
  max_quality: 1080,
  allow_movies: true,
  allow_series: true,
  allow_live: false,
  allow_download: false,
  is_trial: false,
  category_ids: [],
};
