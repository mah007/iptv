import {
  getFavoritesListQueryKey,
  getHomeRetrieveQueryKey,
  getMoviesRetrieveQueryKey,
  getSeriesRetrieveQueryKey,
  useFavoritesAdd,
  useFavoritesDelete,
  useRatingsSet,
  type RatingRequest,
  type Thumb,
  type TitleType,
} from "@smart-iptv/api-portal";
import {
  Button,
  cn,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
  toast,
} from "@smart-iptv/ui";
import { useQueryClient } from "@tanstack/react-query";
import { Check, Clapperboard, Plus, ThumbsDown, ThumbsUp } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

/** The title page, the list and the home rows all show the viewer's state. */
function useInvalidateViewer(type: TitleType, id: string) {
  const queryClient = useQueryClient();
  return () => {
    const detail = type === "movie" ? getMoviesRetrieveQueryKey(id) : getSeriesRetrieveQueryKey(id);
    void queryClient.invalidateQueries({ queryKey: detail });
    void queryClient.invalidateQueries({ queryKey: getFavoritesListQueryKey() });
    void queryClient.invalidateQueries({ queryKey: getHomeRetrieveQueryKey() });
  };
}

/** "My List": add or remove, shown at once and rolled back if the API refuses. */
export function FavoriteButton({
  type,
  id,
  favorite,
}: {
  type: TitleType;
  id: string;
  favorite: boolean;
}) {
  const { t } = useTranslation();
  const [optimistic, setOptimistic] = useState<boolean | null>(null);
  const shown = optimistic ?? favorite;
  const invalidate = useInvalidateViewer(type, id);
  const add = useFavoritesAdd();
  const remove = useFavoritesDelete();

  function toggle(): void {
    const next = !shown;
    setOptimistic(next);
    const settle = {
      onError: () => {
        setOptimistic(null);
        toast.error(t("title.favoriteFailed"));
      },
      onSuccess: () => {
        toast.success(next ? t("title.favoriteAdded") : t("title.favoriteRemoved"));
        invalidate();
      },
    };
    if (next) {
      add.mutate({ data: { title_type: type, title_id: id } }, settle);
    } else {
      remove.mutate({ titleType: type, id }, settle);
    }
  }

  return (
    <Button
      variant="secondary"
      size="lg"
      aria-pressed={shown}
      onClick={toggle}
      className="bg-background/60 backdrop-blur-sm"
    >
      {shown ? <Check aria-hidden="true" /> : <Plus aria-hidden="true" />}
      {t("title.myList")}
    </Button>
  );
}

/** Thumbs up or down; pressing the active one clears it. Feeds "top picks". */
export function RatingButtons({
  type,
  id,
  rating,
}: {
  type: TitleType;
  id: string;
  rating: Thumb | null;
}) {
  const { t } = useTranslation();
  const [optimistic, setOptimistic] = useState<Thumb | null | undefined>(undefined);
  const shown = optimistic === undefined ? rating : optimistic;
  const invalidate = useInvalidateViewer(type, id);
  const rate = useRatingsSet();

  function set(value: Thumb): void {
    const next = shown === value ? null : value;
    setOptimistic(next);
    // The API clears the rating with null (its schema documents it in the description).
    const data = { title_type: type, title_id: id, value: next } as unknown as RatingRequest;
    rate.mutate(
      { data },
      {
        onError: () => {
          setOptimistic(undefined);
          toast.error(t("title.ratingFailed"));
        },
        onSuccess: invalidate,
      },
    );
  }

  return (
    <div role="group" aria-label={t("title.rateLabel")} className="flex items-center gap-1">
      <Button
        variant="secondary"
        size="icon"
        className={cn(
          "size-10 rounded-full bg-background/60 backdrop-blur-sm",
          shown === "up" && "text-primary",
        )}
        aria-pressed={shown === "up"}
        aria-label={t("title.thumbUp")}
        onClick={() => {
          set("up");
        }}
      >
        <ThumbsUp aria-hidden="true" className={cn(shown === "up" && "fill-current")} />
      </Button>
      <Button
        variant="secondary"
        size="icon"
        className={cn(
          "size-10 rounded-full bg-background/60 backdrop-blur-sm",
          shown === "down" && "text-primary",
        )}
        aria-pressed={shown === "down"}
        aria-label={t("title.thumbDown")}
        onClick={() => {
          set("down");
        }}
      >
        <ThumbsDown aria-hidden="true" className={cn(shown === "down" && "fill-current")} />
      </Button>
    </div>
  );
}

/** The trailer in a dialog, from YouTube's privacy-enhanced (no-cookie) player. */
export function TrailerButton({ youtubeKey, title }: { youtubeKey: string; title: string }) {
  const { t, i18n } = useTranslation();
  if (!/^[\w-]{6,20}$/u.test(youtubeKey)) return null;
  const src = `https://www.youtube-nocookie.com/embed/${youtubeKey}?autoplay=1&rel=0&hl=${i18n.language}`;
  return (
    <Dialog>
      <DialogTrigger asChild>
        <Button variant="secondary" size="lg" className="bg-background/60 backdrop-blur-sm">
          <Clapperboard aria-hidden="true" />
          {t("title.trailer")}
        </Button>
      </DialogTrigger>
      <DialogContent className="max-w-4xl p-0 sm:max-w-4xl">
        <DialogHeader className="px-5 pt-5">
          <DialogTitle>{t("title.trailerOf", { title })}</DialogTitle>
          <DialogDescription className="sr-only">{t("title.trailerHint")}</DialogDescription>
        </DialogHeader>
        <div className="aspect-video w-full overflow-hidden rounded-b-card bg-black">
          <iframe
            src={src}
            title={t("title.trailerOf", { title })}
            className="size-full"
            allow="autoplay; encrypted-media; picture-in-picture; fullscreen"
            referrerPolicy="strict-origin-when-cross-origin"
            allowFullScreen
          />
        </div>
      </DialogContent>
    </Dialog>
  );
}
