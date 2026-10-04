"""Engagement (SPEC §6 playback and engagement, §7.9): watch progress, favourites,
thumbs ratings and the nightly similar-titles table.

Titles are typed nullable foreign keys, like the catalogue's artwork and credits
(ADR-0009): exactly one of `movie`/`episode` (progress) or `movie`/`series`
(favourites, ratings), enforced by check constraints, so joins, cascades and
`select_related` stay simple.
"""

from django.conf import settings
from django.db import models

from apps.catalog.models import Episode, Movie, Series
from apps.core.models import BaseModel


def _one_of(first: str, second: str, name: str) -> models.CheckConstraint:
    return models.CheckConstraint(
        condition=models.Q(**{f"{first}__isnull": False, f"{second}__isnull": True})
        | models.Q(**{f"{first}__isnull": True, f"{second}__isnull": False}),
        name=name,
    )


class WatchProgress(BaseModel):
    """Where a customer is in a movie or an episode; one row per customer and title.

    `series` is filled for episodes, so continue-watching can show one entry per series.
    The watch history (SPEC §9 Account, §10 watch-history) is these rows, newest first;
    deleting one also takes the title out of continue-watching.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="watch_progress"
    )
    movie = models.ForeignKey(
        Movie, null=True, blank=True, on_delete=models.CASCADE, related_name="+"
    )
    episode = models.ForeignKey(
        Episode, null=True, blank=True, on_delete=models.CASCADE, related_name="+"
    )
    series = models.ForeignKey(
        Series, null=True, blank=True, on_delete=models.CASCADE, related_name="+"
    )
    position_ms = models.BigIntegerField(default=0)
    duration_ms = models.BigIntegerField(default=0)
    completed = models.BooleanField(default=False)
    #: The playback session that last reported progress (no FK: sessions are history).
    last_session_id = models.UUIDField(null=True, blank=True)

    class Meta:
        ordering = ("-updated_at",)
        constraints = (
            _one_of("movie", "episode", "engagement_progress_one_title"),
            models.UniqueConstraint(
                fields=("user", "movie"),
                condition=models.Q(movie__isnull=False),
                name="engagement_progress_user_movie",
            ),
            models.UniqueConstraint(
                fields=("user", "episode"),
                condition=models.Q(episode__isnull=False),
                name="engagement_progress_user_episode",
            ),
        )
        indexes = (models.Index(fields=("user", "-updated_at"), name="engagement_progress_recent"),)

    def __str__(self) -> str:
        return f"progress:{self.user_id}:{self.movie_id or self.episode_id}"


class Favorite(BaseModel):
    """A title on the customer's "My List"."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="favorites"
    )
    movie = models.ForeignKey(
        Movie, null=True, blank=True, on_delete=models.CASCADE, related_name="+"
    )
    series = models.ForeignKey(
        Series, null=True, blank=True, on_delete=models.CASCADE, related_name="+"
    )

    class Meta:
        ordering = ("-created_at",)
        constraints = (
            _one_of("movie", "series", "engagement_favorite_one_title"),
            models.UniqueConstraint(
                fields=("user", "movie"),
                condition=models.Q(movie__isnull=False),
                name="engagement_favorite_user_movie",
            ),
            models.UniqueConstraint(
                fields=("user", "series"),
                condition=models.Q(series__isnull=False),
                name="engagement_favorite_user_series",
            ),
        )
        indexes = (models.Index(fields=("user", "-created_at"), name="engagement_favorite_user"),)

    def __str__(self) -> str:
        return f"favorite:{self.user_id}:{self.movie_id or self.series_id}"


class Thumb(models.IntegerChoices):
    UP = 1, "Thumbs up"
    DOWN = -1, "Thumbs down"


class Rating(BaseModel):
    """A customer's thumbs up or down on a title (SPEC §6 Rating)."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="ratings"
    )
    movie = models.ForeignKey(
        Movie, null=True, blank=True, on_delete=models.CASCADE, related_name="+"
    )
    series = models.ForeignKey(
        Series, null=True, blank=True, on_delete=models.CASCADE, related_name="+"
    )
    value = models.SmallIntegerField(choices=Thumb.choices)

    class Meta:
        constraints = (
            _one_of("movie", "series", "engagement_rating_one_title"),
            models.UniqueConstraint(
                fields=("user", "movie"),
                condition=models.Q(movie__isnull=False),
                name="engagement_rating_user_movie",
            ),
            models.UniqueConstraint(
                fields=("user", "series"),
                condition=models.Q(series__isnull=False),
                name="engagement_rating_user_series",
            ),
            models.CheckConstraint(
                condition=models.Q(value__in=(1, -1)), name="engagement_rating_value"
            ),
        )

    def __str__(self) -> str:
        return f"rating:{self.user_id}:{self.movie_id or self.series_id}={self.value}"


class SimilarTitle(BaseModel):
    """A title similar to another of the same kind, with its score (SPEC §7.9).

    Recomputed nightly (`engagement.tasks.compute_similar_titles`): the top N per title.
    """

    movie = models.ForeignKey(
        Movie, null=True, blank=True, on_delete=models.CASCADE, related_name="similar_rows"
    )
    similar_movie = models.ForeignKey(
        Movie, null=True, blank=True, on_delete=models.CASCADE, related_name="+"
    )
    series = models.ForeignKey(
        Series, null=True, blank=True, on_delete=models.CASCADE, related_name="similar_rows"
    )
    similar_series = models.ForeignKey(
        Series, null=True, blank=True, on_delete=models.CASCADE, related_name="+"
    )
    score = models.FloatField()

    class Meta:
        ordering = ("-score",)
        constraints = (
            models.CheckConstraint(
                condition=models.Q(
                    movie__isnull=False,
                    similar_movie__isnull=False,
                    series__isnull=True,
                    similar_series__isnull=True,
                )
                | models.Q(
                    movie__isnull=True,
                    similar_movie__isnull=True,
                    series__isnull=False,
                    similar_series__isnull=False,
                ),
                name="engagement_similar_same_kind",
            ),
        )
        indexes = (
            models.Index(fields=("movie", "-score"), name="engagement_similar_movie"),
            models.Index(fields=("series", "-score"), name="engagement_similar_series"),
        )

    def __str__(self) -> str:
        return f"similar:{self.movie_id or self.series_id}"
