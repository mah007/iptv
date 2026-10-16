"""The rights recorded with an import reach the title its file is matched to (ADR-0025).

SPEC §1.1: every title carries `rights_holder`, `license_ref` and `license_expires_at`.
An admin starting an import states them for the files it brings; when the scanner's
file is matched (`apps.metadata.services.accept_movie`, `link_episode_file`), each of
those fields the title doesn't have yet is filled from the import. A field the title
already has, or one an admin edited (locked), is left alone.
"""

from django.utils import timezone

from apps.catalog.models import MediaFile, Title
from apps.imports.models import ImportFile

RIGHTS_FIELDS = ("rights_holder", "license_ref", "license_expires_at")


def apply_import_rights(file: MediaFile, title: Title) -> list[str]:
    """Fill the title's empty, unlocked rights fields from the import that brought
    `file`; returns the fields set. Call before the title's status is refreshed (an
    expiry date in the past hides it)."""
    record = (
        ImportFile.objects.select_related("job")
        .filter(library_id=file.library_id, storage_key=file.storage_key)
        .order_by("-created_at")
        .first()
    )
    if record is None:
        return []
    job = record.job
    locked = set(title.metadata_locked_fields or ())
    changes = {
        name: getattr(job, name)
        for name in RIGHTS_FIELDS
        if name not in locked and not getattr(title, name) and getattr(job, name)
    }
    now = timezone.now()
    if changes:
        type(title)._default_manager.filter(pk=title.pk).update(**changes, updated_at=now)
        for name, value in changes.items():
            setattr(title, name, value)
    if record.rights_applied_at is None:
        ImportFile.objects.filter(pk=record.pk).update(rights_applied_at=now, updated_at=now)
    return list(changes)
