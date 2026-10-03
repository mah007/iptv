import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { renderWithUi } from "../test-utils";
import { STATUS_TONES, StatusBadge } from "./status-badge";

describe("StatusBadge", () => {
  it.each([
    ["active", "Active", "text-success-text"],
    ["grace", "Grace", "text-warning-text"],
    ["expired", "Expired", "text-neutral-text"],
    ["suspended", "Suspended", "text-danger-text"],
    ["trial", "Trial", "text-violet-text"],
    ["processing", "Processing", "text-info-text"],
    ["review", "Review", "text-warning-text"],
    ["ready", "Ready", "text-success-text"],
    ["hidden", "Hidden", "text-neutral-text"],
  ])("shows %s as %s in the SPEC colour", (status, label, colour) => {
    renderWithUi(<StatusBadge status={status} />);
    const badge = screen.getByText(label);
    expect(badge.className).toContain(colour);
    expect(badge.getAttribute("data-status")).toBe(status);
  });

  it("maps every known status to a tone", () => {
    for (const tone of Object.values(STATUS_TONES)) {
      expect(["success", "warning", "danger", "info", "violet", "neutral"]).toContain(tone);
    }
  });

  it("translates the label", () => {
    renderWithUi(<StatusBadge status="suspended" />, { language: "ar" });
    expect(screen.getByText("موقوف")).toBeTruthy();
  });

  it("shows an unknown status neutrally with its raw value", () => {
    renderWithUi(<StatusBadge status="archived" />);
    expect(screen.getByText("archived").className).toContain("text-neutral-text");
  });

  it("accepts a label override", () => {
    renderWithUi(<StatusBadge status="trial" label="Trial · 3 days left" />);
    expect(screen.getByText("Trial · 3 days left").className).toContain("text-violet-text");
  });
});
