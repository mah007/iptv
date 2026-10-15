import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Markdown, parseMarkdown } from "./markdown";

describe("parseMarkdown", () => {
  it("splits headings, paragraphs and lists", () => {
    const text = [
      "# Title",
      "",
      "First line",
      "continues here.",
      "",
      "## Rights",
      "- one",
      "- two",
      "  wrapped",
      "1. first",
      "2. second",
      "After the list.",
    ].join("\n");
    expect(parseMarkdown(text)).toEqual([
      { type: "heading", level: 1, text: "Title" },
      { type: "paragraph", text: "First line continues here." },
      { type: "heading", level: 2, text: "Rights" },
      { type: "list", ordered: false, items: ["one", "two wrapped"] },
      { type: "list", ordered: true, items: ["first", "second"] },
      { type: "paragraph", text: "After the list." },
    ]);
  });

  it("treats deeper headings and HTML as text", () => {
    expect(parseMarkdown("#### Deep\n<script>alert(1)</script>")).toEqual([
      { type: "paragraph", text: "#### Deep <script>alert(1)</script>" },
    ]);
  });
});

describe("Markdown", () => {
  it("renders bold and safe links only, never HTML", () => {
    const { container } = render(
      <Markdown
        text={[
          "# Privacy",
          "Write to [support](mailto:help@example.com) or see [the site](https://example.com).",
          "**Bold** and [a trap](javascript:alert(1)) and <b>tags</b>.",
        ].join("\n\n")}
      />,
    );
    expect(screen.getByRole("heading", { level: 1, name: "Privacy" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "support" }).getAttribute("href")).toBe(
      "mailto:help@example.com",
    );
    const site = screen.getByRole("link", { name: "the site" });
    expect(site.getAttribute("rel")).toBe("noopener noreferrer");
    expect(screen.queryByRole("link", { name: "a trap" })).toBeNull();
    expect(screen.getByText("Bold").tagName).toBe("STRONG");
    expect(container.querySelector("b")).toBeNull();
    expect(container.textContent).toContain("<b>tags</b>");
  });

  it("lists items", () => {
    render(<Markdown text={"- Email: a@example.com\n- Phone: 123"} />);
    expect(screen.getAllByRole("listitem").map((item) => item.textContent)).toEqual([
      "Email: a@example.com",
      "Phone: 123",
    ]);
  });
});
