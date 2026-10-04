import type { TitleCard as TitleCardData } from "@smart-iptv/api-portal";
import { cn, PosterCard } from "@smart-iptv/ui";
import { Link } from "@tanstack/react-router";

import { artworkSource } from "../lib/artwork";
import { titlePath } from "../lib/links";

/** A poster linking to the title's page, with year and rating on hover or focus. */
export function TitleCard({ title, className }: { title: TitleCardData; className?: string }) {
  return (
    <PosterCard
      asChild
      title={title.title}
      year={title.year}
      rating={title.rating}
      poster={artworkSource(title.poster)}
      blurhash={title.poster?.blurhash}
      className={cn("w-full", className)}
    >
      <Link {...titlePath(title.type, title.id)} />
    </PosterCard>
  );
}
