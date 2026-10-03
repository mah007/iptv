from django.core.exceptions import ValidationError
from django.db import models

from apps.core.db import NextVal
from apps.core.models import BaseModel

CATEGORY_XC_ID_SEQUENCE = "catalog_category_xc_id_seq"


class CategoryKind(models.TextChoices):
    VOD = "vod", "Movies"
    SERIES = "series", "Series"
    LIVE = "live", "Live TV"


class Category(BaseModel):
    """A browse category for movies, series or live channels (SPEC §6 catalog).

    Customer access profiles restrict playback to categories (SPEC §7.4); M6 adds
    the titles. `xc_id` is the integer id Xtream clients see as `category_id`.
    """

    xc_id = models.BigIntegerField(unique=True, db_default=NextVal(CATEGORY_XC_ID_SEQUENCE))
    kind = models.CharField(max_length=8, choices=CategoryKind.choices)
    name_en = models.CharField(max_length=100)
    name_ar = models.CharField(max_length=100)
    slug = models.SlugField(max_length=100)
    sort = models.IntegerField(default=0)
    is_adult = models.BooleanField(default=False)
    visible_in_xtream = models.BooleanField(default=True)
    icon = models.CharField(max_length=64, blank=True)
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="children"
    )

    class Meta:
        ordering = ("kind", "sort", "name_en")
        verbose_name_plural = "categories"
        constraints = (
            models.UniqueConstraint(fields=("kind", "slug"), name="catalog_category_kind_slug"),
        )

    def __str__(self) -> str:
        return f"{self.kind}:{self.slug}"

    def clean(self) -> None:
        if self.parent_id is not None:
            if self.parent_id == self.pk:
                raise ValidationError({"parent": "A category cannot be its own parent."})
            if self.parent is not None and self.parent.kind != self.kind:
                raise ValidationError({"parent": "The parent must be of the same kind."})
