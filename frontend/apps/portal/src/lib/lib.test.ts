import { describe, expect, it } from "vitest";

import { ACCESS, ME } from "../test/fixtures";
import { parseBrowseSearch, parseLoginSearch, parseResetSearch, parseWatchSearch } from "../search";
import { accessState } from "./access";
import { artworkSource, artworkUrl } from "./artwork";
import { isPublicPath, safeRedirect } from "./auth";
import { cursorOf } from "./cursor";
import { languageName, languageTag } from "./languages";
import { titlePath, watchPath } from "./links";
import { localName } from "./local-name";

describe("safeRedirect", () => {
  it("keeps paths on this site and refuses everything else", () => {
    expect(safeRedirect("/movies?genre=1")).toBe("/movies?genre=1");
    expect(safeRedirect(undefined)).toBe("/");
    expect(safeRedirect("//evil.example")).toBe("/");
    expect(safeRedirect("/\\evil.example")).toBe("/");
    expect(safeRedirect("https://evil.example")).toBe("/");
    expect(safeRedirect("/login?redirect=/x")).toBe("/");
    expect(safeRedirect("/reset-password?uid=a")).toBe("/");
  });

  it("knows the pages that work signed out", () => {
    expect(isPublicPath("/login")).toBe(true);
    expect(isPublicPath("/forgot-password")).toBe(true);
    expect(isPublicPath("/movies")).toBe(false);
  });
});

describe("cursorOf", () => {
  it("reads the cursor of a next link, absolute or relative", () => {
    expect(cursorOf("http://app.localhost/api/v1/movies?cursor=cD0y&page_size=2")).toBe("cD0y");
    expect(cursorOf("/api/v1/series?page_size=2&cursor=abc%3D")).toBe("abc=");
    expect(cursorOf(null)).toBeUndefined();
    expect(cursorOf("/api/v1/movies")).toBeUndefined();
  });
});

describe("artwork", () => {
  const art = {
    url: "http://m/x/w500.webp",
    sizes: { w185: { webp: "http://m/x/w185.webp", avif: "http://m/x/w185.avif" } },
    width: 500,
    height: 750,
    blurhash: "",
  };

  it("resolves a size and format, falling back to the default URL", () => {
    const resolve = artworkSource(art);
    expect(resolve?.("w185", "avif")).toBe("http://m/x/w185.avif");
    expect(resolve?.("w1280", "webp")).toBe("http://m/x/w500.webp");
    expect(artworkSource(null)).toBeNull();
    expect(artworkUrl(art, "w185")).toBe("http://m/x/w185.webp");
    expect(artworkUrl(undefined, "w185")).toBeNull();
  });
});

describe("links", () => {
  it("routes titles and the player", () => {
    expect(titlePath("movie", "m1")).toEqual({ to: "/movies/$titleId", params: { titleId: "m1" } });
    expect(titlePath("series", "s1").to).toBe("/series/$titleId");
    expect(watchPath("series", "s1", { episode: "e2" })).toEqual({
      to: "/watch/series/$titleId",
      params: { titleId: "s1" },
      search: { episode: "e2" },
    });
    expect(watchPath("movie", "m1", { restart: true }).search).toEqual({ restart: true });
  });
});

describe("languages", () => {
  it("names ISO 639-2 track languages in the UI language", () => {
    expect(languageName("ara", "en")).toBe("Arabic");
    expect(languageName("eng", "ar")).toBe("الإنجليزية");
    expect(languageName("und", "en")).toBeNull();
    expect(languageTag("ara")).toBe("ar");
  });

  it("picks the Arabic plan name in Arabic", () => {
    expect(localName({ name_en: "Monthly", name_ar: "شهري" }, "ar")).toBe("شهري");
    expect(localName({ name_en: "Monthly", name_ar: "" }, "ar")).toBe("Monthly");
    expect(localName({ name_en: "Monthly", name_ar: "شهري" }, "en")).toBe("Monthly");
  });
});

describe("search params", () => {
  it("keeps only valid values", () => {
    expect(parseBrowseSearch({ genre: "g1", year: "1999", sort: "rating", junk: 1 })).toEqual({
      genre: "g1",
      year: 1999,
      sort: "rating",
    });
    expect(parseBrowseSearch({ year: "abc", sort: "loudest" })).toEqual({});
    expect(parseLoginSearch({ redirect: "/x", expired: "true" })).toEqual({
      redirect: "/x",
      expired: true,
    });
    expect(parseResetSearch({ uid: "MQ", token: "t-1", welcome: 1 })).toEqual({
      uid: "MQ",
      token: "t-1",
      welcome: true,
    });
    expect(parseWatchSearch({ episode: "e1", restart: "true" })).toEqual({
      episode: "e1",
      restart: true,
    });
  });
});

describe("accessState", () => {
  const now = new Date("2026-10-04T12:00:00Z");
  const subscription = {
    id: "s1",
    plan: { id: "p", code: "m", name_en: "M", name_ar: "", is_trial: false },
    status: "active" as const,
    starts_at: "2026-09-04T00:00:00Z",
    ends_at: "2026-11-04T00:00:00Z",
    grace_until: null,
    source: "payment" as const,
    max_streams: 2,
    max_devices: 2,
    max_quality: 1080,
    ended_at: null,
    end_reason: "expired" as const,
  };

  it("follows the subscription when there is one", () => {
    expect(accessState(ME, { subscription, pending: null }, now).kind).toBe("active");
    expect(
      accessState(
        ME,
        {
          subscription: { ...subscription, status: "grace", grace_until: "2026-10-07T00:00:00Z" },
          pending: null,
        },
        now,
      ),
    ).toEqual({ kind: "grace", graceUntil: "2026-10-07T00:00:00Z" });
    expect(
      accessState(ME, { subscription: { ...subscription, status: "expired" }, pending: null }, now)
        .kind,
    ).toBe("expired");
    expect(
      accessState(
        ME,
        { subscription: { ...subscription, status: "expired" }, pending: subscription },
        now,
      ).kind,
    ).toBe("active");
  });

  it("falls back to the access profile, and a suspended account wins", () => {
    expect(accessState(ME, { subscription: null, pending: null }, now).kind).toBe("active");
    const expired = { ...ME, access: { ...ACCESS, expires_at: "2026-10-01T00:00:00Z" } };
    expect(accessState(expired, { subscription: null, pending: null }, now).kind).toBe("expired");
    expect(accessState({ ...ME, status: "suspended" }, undefined, now).kind).toBe("suspended");
    expect(accessState(undefined, undefined, now).kind).toBe("none");
  });
});
