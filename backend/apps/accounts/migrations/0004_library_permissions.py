"""Split library permissions (POC slice 2): `library.view` and `library.manage` join
`library.review`.

Seed roles that already existed get the new grants their defaults now include
(`sync_rbac` only grants defaults to roles it creates): every role that could manage
the library through `library.review` keeps that power through `library.manage`, and the
viewer role can see the library.
"""

from django.db import migrations

NEW_GRANTS = {
    "admin": ("library.view", "library.manage"),
    "content_manager": ("library.view", "library.manage"),
    "viewer": ("library.view",),
}


def grant(apps, schema_editor):
    from apps.accounts.rbac import sync_rbac

    Permission = apps.get_model("accounts", "Permission")
    Role = apps.get_model("accounts", "Role")
    sync_rbac(Permission, Role)
    for role in Role.objects.filter(name__in=NEW_GRANTS):
        role.permissions.add(*Permission.objects.filter(code__in=NEW_GRANTS[role.name]))
    # Custom roles that held library.review could manage libraries before the split.
    review = Permission.objects.filter(code="library.review").first()
    if review is not None:
        managed = Permission.objects.filter(code__in=("library.view", "library.manage"))
        for role in Role.objects.filter(permissions=review).exclude(name__in=NEW_GRANTS):
            role.permissions.add(*managed)


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0003_seed_rbac"),
    ]

    operations = [migrations.RunPython(grant, migrations.RunPython.noop)]
