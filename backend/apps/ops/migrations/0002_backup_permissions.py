"""The backup permissions (H2, ADR-0020): `backups.view` and `backups.run`.

Seed roles that already exist get the grants their defaults now include (`sync_rbac`
only grants defaults to roles it creates): admin both, viewer the view. Owners hold
every permission anyway.
"""

from django.db import migrations

NEW_GRANTS = {
    "admin": ("backups.view", "backups.run"),
    "viewer": ("backups.view",),
}


def grant(apps, schema_editor):
    from apps.accounts.rbac import sync_rbac

    Permission = apps.get_model("accounts", "Permission")
    Role = apps.get_model("accounts", "Role")
    sync_rbac(Permission, Role)
    for role in Role.objects.filter(name__in=NEW_GRANTS):
        role.permissions.add(*Permission.objects.filter(code__in=NEW_GRANTS[role.name]))


class Migration(migrations.Migration):
    dependencies = [
        ("ops", "0001_initial"),
        ("accounts", "0005_monitoring_permission"),
    ]

    operations = [migrations.RunPython(grant, migrations.RunPython.noop)]
