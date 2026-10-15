import type { ReactNode } from "react";

import { cn } from "../lib/cn";

/*
 * A small Markdown subset as React elements (ADR-0023): the admin's legal pages.
 * Headings (#, ##, ###), paragraphs, bulleted and numbered lists, **bold** and
 * [links](https://…) (https, http and mailto only). Anything else is plain text:
 * no raw HTML is ever produced (the CSP and ESLint forbid it).
 */

export type MarkdownBlock =
  | { type: "heading"; level: 1 | 2 | 3; text: string }
  | { type: "paragraph"; text: string }
  | { type: "list"; ordered: boolean; items: string[] };

const HEADING = /^(#{1,3})\s+(.*)$/u;
const BULLET = /^\s*[-*]\s+(.*)$/u;
const NUMBERED = /^\s*\d+[.)]\s+(.*)$/u;

/** Split the text into blocks. */
export function parseMarkdown(text: string): MarkdownBlock[] {
  const blocks: MarkdownBlock[] = [];
  let paragraph: string[] = [];
  let list: { ordered: boolean; items: string[] } | null = null;
  const flush = () => {
    if (paragraph.length > 0) {
      blocks.push({ type: "paragraph", text: paragraph.join(" ") });
      paragraph = [];
    }
    if (list !== null) {
      blocks.push({ type: "list", ...list });
      list = null;
    }
  };
  for (const raw of text.replace(/\r\n?/gu, "\n").split("\n")) {
    const line = raw.trimEnd();
    if (line.trim() === "") {
      flush();
      continue;
    }
    const heading = HEADING.exec(line);
    if (heading) {
      flush();
      const level = (heading[1] ?? "#").length as 1 | 2 | 3;
      blocks.push({ type: "heading", level, text: (heading[2] ?? "").trim() });
      continue;
    }
    const bullet = BULLET.exec(line);
    const numbered = bullet ? null : NUMBERED.exec(line);
    const item = bullet ?? numbered;
    if (item) {
      const ordered = numbered !== null;
      if (paragraph.length > 0 || (list !== null && list.ordered !== ordered)) flush();
      list ??= { ordered, items: [] };
      list.items.push((item[1] ?? "").trim());
      continue;
    }
    if (list !== null && /^\s+\S/u.test(raw)) {
      // An indented line continues the list item above it.
      const last = list.items.length - 1;
      list.items[last] = `${list.items[last] ?? ""} ${line.trim()}`;
      continue;
    }
    if (list !== null) flush();
    paragraph.push(line.trim());
  }
  flush();
  return blocks;
}

const INLINE = /\*\*(.+?)\*\*|\[([^\]]+)\]\(([^)\s]+)\)/gu;
const SAFE_LINK = /^(?:https?:\/\/|mailto:)/iu;

/** Bold and links inside a block; unsafe links stay text. */
export function renderInline(text: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  let last = 0;
  for (const match of text.matchAll(INLINE)) {
    const index = match.index;
    if (index > last) nodes.push(text.slice(last, index));
    const [whole, bold, label, href] = match;
    if (bold !== undefined) {
      nodes.push(<strong key={index}>{bold}</strong>);
    } else if (label !== undefined && href !== undefined && SAFE_LINK.test(href)) {
      const external = !href.toLowerCase().startsWith("mailto:");
      nodes.push(
        <a
          key={index}
          href={href}
          className="font-medium text-primary underline underline-offset-4"
          {...(external ? { target: "_blank", rel: "noopener noreferrer" } : {})}
        >
          {label}
        </a>,
      );
    } else {
      nodes.push(label ?? whole);
    }
    last = index + whole.length;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

const HEADING_CLASS = {
  1: "text-2xl font-semibold text-foreground sm:text-3xl",
  2: "pt-2 text-lg font-semibold text-foreground",
  3: "font-semibold text-foreground",
} as const;

export interface MarkdownProps {
  text: string;
  className?: string;
}

/** The Markdown subset above, as a readable document (logical properties: RTL-safe). */
export function Markdown({ text, className }: MarkdownProps) {
  return (
    <div
      data-slot="markdown"
      dir="auto"
      className={cn("grid gap-3 text-sm leading-relaxed text-foreground", className)}
    >
      {parseMarkdown(text).map((block, index) => {
        const key = `${block.type}-${String(index)}`;
        switch (block.type) {
          case "heading": {
            const Tag = `h${String(block.level)}` as "h1" | "h2" | "h3";
            return (
              <Tag key={key} className={HEADING_CLASS[block.level]}>
                {renderInline(block.text)}
              </Tag>
            );
          }
          case "paragraph":
            return <p key={key}>{renderInline(block.text)}</p>;
          case "list": {
            const List = block.ordered ? "ol" : "ul";
            return (
              <List
                key={key}
                className={cn("grid gap-1.5 ps-5", block.ordered ? "list-decimal" : "list-disc")}
              >
                {block.items.map((item, position) => (
                  <li key={`${key}-${String(position)}`}>{renderInline(item)}</li>
                ))}
              </List>
            );
          }
        }
      })}
    </div>
  );
}
