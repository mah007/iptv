import { describe, expect, it } from "vitest";

import {
  createFormatters,
  formatBitrate,
  formatBytes,
  formatDuration,
  formatLocale,
  formatNumber,
  isoDuration,
} from "./format";

const ARABIC_INDIC_DIGITS = /[٠-٩]/u;

describe("formatBytes", () => {
  it.each([
    [0, "0 byte"],
    [999, "999 byte"],
    [12_300, "12.3 kB"],
    [512_000_000, "512 MB"],
    [1_500_000_000, "1.5 GB"],
    [2_250_000_000_000, "2.3 TB"],
  ])("formats %d bytes as %s", (bytes, expected) => {
    expect(formatBytes(bytes)).toBe(expected);
  });

  it("uses Latin digits in Arabic", () => {
    const text = formatBytes(1_500_000_000, "ar");
    expect(text.startsWith("1.5 ")).toBe(true);
    expect(text).not.toMatch(ARABIC_INDIC_DIGITS);
  });

  it("treats garbage as zero", () => {
    expect(formatBytes(Number.NaN)).toBe("0 byte");
    expect(formatBytes(-5)).toBe("0 byte");
  });
});

describe("formatBitrate", () => {
  it("shows megabits with one fixed decimal", () => {
    expect(formatBitrate(8_240_000)).toBe("8.2 Mb/s");
    expect(formatBitrate(12_000_000)).toBe("12.0 Mb/s");
    expect(formatBitrate(8_240_000, "ar")).toMatch(/^8\.2 /u);
  });
});

describe("formatDuration", () => {
  it("prints a clock", () => {
    expect(formatDuration(4325)).toBe("1:12:05");
    expect(formatDuration(65)).toBe("1:05");
    expect(formatDuration(0)).toBe("0:00");
    expect(formatDuration(4325.9)).toBe("1:12:05");
  });

  it("prints the two largest units in short style", () => {
    expect(formatDuration(4325, "en", "short")).toBe("1 hr 12 mins");
    expect(formatDuration(725, "en", "short")).toBe("12 mins 5 secs");
    expect(formatDuration(3600, "en", "short")).toBe("1 hr");
    expect(formatDuration(45, "en", "short")).toBe("45 secs");
    expect(formatDuration(0, "en", "short")).toBe("0 secs");
  });

  it("speaks Arabic with Latin digits", () => {
    expect(formatDuration(4325, "ar", "short")).toBe("1 س و12 د");
    expect(formatDuration(4325, "ar")).toBe("1:12:05");
  });

  it("gives an ISO 8601 duration for <time>", () => {
    expect(isoDuration(4325)).toBe("PT1H12M5S");
  });
});

describe("formatNumber and locales", () => {
  it("groups with Latin digits by default, in both languages", () => {
    expect(formatNumber(1_234_567.5)).toBe("1,234,567.5");
    expect(formatNumber(1_234_567.5, "ar")).toBe("1,234,567.5");
  });

  it("switches Arabic to Arabic-Indic digits only when asked", () => {
    expect(formatLocale("ar")).toBe("ar-SA-u-ca-gregory-nu-latn");
    expect(formatLocale("ar-SA")).toBe("ar-SA-u-ca-gregory-nu-latn");
    expect(createFormatters("ar", "Asia/Riyadh", "native").number(42)).toBe("٤٢");
    expect(createFormatters("en", "Asia/Riyadh", "native").number(42)).toBe("42");
  });

  it("exposes the media helpers on the bound formatters", () => {
    const format = createFormatters("en");
    expect(format.bytes(1_500_000_000)).toBe("1.5 GB");
    expect(format.bitrate(8_240_000)).toBe("8.2 Mb/s");
    expect(format.duration(4325, "short")).toBe("1 hr 12 mins");
  });
});
