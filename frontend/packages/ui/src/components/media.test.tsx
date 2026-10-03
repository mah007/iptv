import { fireEvent, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { nth, renderWithUi } from "../test-utils";
import { PosterCard, PosterGrid } from "./poster-card";
import { BackdropImage, PosterImage } from "./poster-image";
import { ProgressBar } from "./progress-bar";
import { QualityBadges, qualityKeys } from "./quality-badges";
import { StatusBadge } from "./status-badge";

// Sample hash from the blurhash reference implementation.
const BLURHASH = "LEHV6nWB2yk8pyo0adR*.7kCMdnj";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("PosterImage", () => {
  it("offers AVIF then WebP at w185/w500, lazily, over a blurhash placeholder", () => {
    const { container } = renderWithUi(
      <PosterImage src="/images/m42/poster/" blurhash={BLURHASH} alt="The Matrix poster" />,
    );
    const [avif, webp] = Array.from(container.querySelectorAll("source"));
    expect(avif?.getAttribute("type")).toBe("image/avif");
    expect(avif?.getAttribute("srcset")).toBe(
      "/images/m42/poster/w185.avif 185w, /images/m42/poster/w500.avif 500w",
    );
    expect(webp?.getAttribute("type")).toBe("image/webp");
    expect(webp?.getAttribute("srcset")).toBe(
      "/images/m42/poster/w185.webp 185w, /images/m42/poster/w500.webp 500w",
    );

    const img = screen.getByRole("img", { name: "The Matrix poster" });
    expect(img.getAttribute("src")).toBe("/images/m42/poster/w500.webp");
    expect(img.getAttribute("loading")).toBe("lazy");
    expect(container.querySelector("[data-slot=blurhash]")).toBeTruthy();
    expect(container.querySelector("[data-slot=poster-image]")?.className).toContain(
      "aspect-[2/3]",
    );

    fireEvent.load(img);
    expect(container.querySelector("[data-slot=blurhash]")).toBeNull();
    expect(container.querySelector("[data-slot=poster-image]")?.getAttribute("data-state")).toBe(
      "loaded",
    );
  });

  it("paints the decoded blurhash on the canvas", () => {
    const putImageData = vi.fn();
    const context = {
      createImageData: (width: number, height: number) => ({
        data: new Uint8ClampedArray(width * height * 4),
      }),
      putImageData,
    };
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(
      context as unknown as CanvasRenderingContext2D,
    );
    renderWithUi(<PosterImage src="/images/m42/poster" blurhash={BLURHASH} alt="" />);
    expect(putImageData).toHaveBeenCalledTimes(1);
    const image = nth(putImageData.mock.calls, 0)[0] as { data: Uint8ClampedArray };
    expect(image.data.length).toBe(20 * 30 * 4);
    expect(image.data.some((value) => value > 0)).toBe(true);
  });

  it("falls back to an icon, keeping the alt text, when the image fails", () => {
    const { container } = renderWithUi(<PosterImage src="/images/x" alt="Dune poster" />);
    fireEvent.error(screen.getByRole("img", { name: "Dune poster" }));
    expect(container.querySelector("picture")).toBeNull();
    const fallback = screen.getByRole("img", { name: "Dune poster" });
    expect(fallback.getAttribute("data-slot")).toBe("artwork-fallback");
  });

  it("shows a decorative placeholder when there is no image", () => {
    const { container } = renderWithUi(<PosterImage src={null} alt="" />);
    expect(screen.queryByRole("img")).toBeNull();
    expect(
      container.querySelector("[data-slot=artwork-fallback]")?.getAttribute("aria-hidden"),
    ).toBe("true");
  });

  it("accepts a URL resolver and loads hero backdrops eagerly at 16:9", () => {
    const { container } = renderWithUi(
      <BackdropImage
        src={(size, format) => `https://cdn.example.com/b/${size}.${format}?sig=1`}
        alt=""
        priority
      />,
    );
    expect(container.querySelector("source")?.getAttribute("srcset")).toBe(
      "https://cdn.example.com/b/w780.avif?sig=1 780w, https://cdn.example.com/b/w1280.avif?sig=1 1280w",
    );
    const img = container.querySelector("img");
    expect(img?.getAttribute("loading")).toBe("eager");
    expect(img?.getAttribute("fetchpriority")).toBe("high");
    expect(container.querySelector("[data-slot=backdrop-image]")?.className).toContain(
      "aspect-video",
    );
  });
});

describe("QualityBadges", () => {
  it("shows compact badges with spoken full names, in a fixed order", () => {
    renderWithUi(
      <QualityBadges
        media={{
          width: 1920,
          height: 1080,
          hdr: "hdr10",
          audio_channels: 6,
          video_codec: "av1",
        }}
      />,
    );
    const list = screen.getByRole("list", { name: "Quality" });
    const keys = within(list)
      .getAllByRole("listitem")
      .map((item) => item.getAttribute("data-quality"));
    expect(keys).toEqual(["fhd", "hdr10", "surround51", "av1"]);
    expect(screen.getByText("1080p").getAttribute("aria-hidden")).toBe("true");
    expect(screen.getByText("Full HD 1080p").className).toContain("sr-only");
  });

  it.each([
    [{ width: 3840, height: 1600 }, ["uhd"]],
    [{ height: 720 }, ["hd"]],
    [{ height: 480 }, ["sd"]],
    [{ hdr: "sdr", video_codec: "h264", audio_channels: 2 }, []],
    [
      { hdr: "DV", audio_channels: 8, has_atmos: true, video_codec: "HEVC" },
      ["dv", "surround71", "atmos", "hevc"],
    ],
    [{ hdr: "hlg" }, ["hlg"]],
  ])("classifies %j as %j", (media, expected) => {
    expect(qualityKeys(media)).toEqual(expected);
  });

  it("renders nothing when nothing is notable", () => {
    const { container } = renderWithUi(<QualityBadges media={{ video_codec: "h264" }} />);
    expect(container.querySelector("[data-slot=quality-badges]")).toBeNull();
  });

  it("speaks Arabic", () => {
    renderWithUi(<QualityBadges media={{ audio_channels: 6 }} />, { language: "ar" });
    expect(screen.getByRole("list", { name: "الجودة" })).toBeTruthy();
    expect(screen.getByText("صوت محيطي 5.1")).toBeTruthy();
  });
});

describe("PosterCard and PosterGrid", () => {
  it("is one link named by the title and described by its details", () => {
    renderWithUi(
      <PosterCard
        href="/library/movies/42"
        title="The Matrix"
        year={1999}
        rating={8.2}
        poster="/images/m42/poster"
        media={{ width: 3840, height: 1600, hdr: "dv", video_codec: "hevc" }}
        subtitles={["ara", "en", "eng"]}
        badges={<StatusBadge status="ready" />}
      />,
    );
    const link = screen.getByRole("link", {
      name: "The Matrix",
      description:
        "1999, Rated 8.2 out of 10, 4K Ultra HD, Dolby Vision, HEVC video, Subtitles: Arabic and English Ready",
    });
    expect(link.getAttribute("href")).toBe("/library/movies/42");
    // The poster is decorative next to the printed title.
    expect(screen.queryByRole("img")).toBeNull();
    const overlay = link.querySelector("[data-slot=poster-overlay]");
    expect(overlay?.getAttribute("aria-hidden")).toBe("true");
    expect(overlay?.textContent).toContain("8.2");
    expect(overlay?.textContent).toContain("AR · EN");
  });

  it("renders a router link through asChild", () => {
    renderWithUi(
      <PosterCard asChild title="Dune">
        <a href="/library/movies/7" data-router="link" />
      </PosterCard>,
    );
    const link = screen.getByRole("link", { name: "Dune" });
    expect(link.getAttribute("data-router")).toBe("link");
    expect(link.getAttribute("data-slot")).toBe("poster-card");
    expect(link.getAttribute("href")).toBe("/library/movies/7");
  });

  it("shows skeleton cards while loading", () => {
    renderWithUi(<PosterGrid loading skeletonCount={3} aria-label="Movies" />);
    const grid = screen.getByRole("list", { name: "Movies" });
    expect(grid.getAttribute("aria-busy")).toBe("true");
    expect(grid.querySelectorAll("[data-slot=poster-card-skeleton]")).toHaveLength(3);
  });

  it("wraps each card in a list item", () => {
    renderWithUi(
      <PosterGrid aria-label="Series">
        <PosterCard key="a" href="/a" title="Alpha" />
        <PosterCard key="b" href="/b" title="Bravo" />
      </PosterGrid>,
    );
    const grid = screen.getByRole("list", { name: "Series" });
    expect(within(grid).getAllByRole("listitem")).toHaveLength(2);
    expect(within(grid).getByRole("link", { name: "Bravo" })).toBeTruthy();
  });
});

describe("ProgressBar", () => {
  it("reports value, percentage, ETA and speed", () => {
    renderWithUi(<ProgressBar label="Transcoding" value={42} etaSeconds={185} speed={1.8} />);
    const bar = screen.getByRole("progressbar", { name: "Transcoding" });
    expect(bar.getAttribute("aria-valuenow")).toBe("42");
    expect(bar.getAttribute("aria-valuemax")).toBe("100");
    expect(bar.getAttribute("aria-valuetext")).toBe("42%, 3 mins 5 secs left");
    expect(screen.getByText("42%")).toBeTruthy();
    expect(screen.getByText("1.8×")).toBeTruthy();
    expect(screen.getByText("3 mins 5 secs left")).toBeTruthy();
  });

  it("is indeterminate without a value", () => {
    renderWithUi(<ProgressBar aria-label="Scanning" />);
    const bar = screen.getByRole("progressbar", { name: "Scanning" });
    expect(bar.hasAttribute("aria-valuenow")).toBe(false);
    expect(bar.getAttribute("data-state")).toBe("indeterminate");
    expect(bar.firstElementChild?.className).toContain("animate-indeterminate");
  });

  it("clamps out-of-range values instead of going indeterminate", () => {
    renderWithUi(<ProgressBar aria-label="Upload" value={150} />);
    const bar = screen.getByRole("progressbar", { name: "Upload" });
    expect(bar.getAttribute("aria-valuenow")).toBe("100");
    expect(bar.getAttribute("data-state")).toBe("complete");
  });

  it("has a slim inline variant for table cells", () => {
    const { container } = renderWithUi(
      <ProgressBar variant="inline" label="Job 12" value={0.5} max={1} />,
    );
    const bar = screen.getByRole("progressbar", { name: "Job 12" });
    expect(bar.className).toContain("h-1.5");
    expect(screen.getByText("50%")).toBeTruthy();
    expect(screen.getByText("Job 12").className).toContain("sr-only");
    expect(container.querySelector("[data-slot=progress]")?.className).toContain("items-center");
  });
});
