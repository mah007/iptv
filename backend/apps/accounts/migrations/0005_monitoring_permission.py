"""The monitoring permission (O1, ADR-0018): `monitoring.view` opens Grafana, Prometheus
and Alertmanager on grafana.<domain> through the admin's sign-in.

Seed roles that already exist get the grant their defaults now include (`sync_rbac`
only grants defaults to roles it creates): admin and viewer. Owners hold every
permission anyway.
"""

from django.db import migrations

NEW_GRANTS = {
    "admin": ("monitoring.view",),
    "viewer": ("monitoring.view",),
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
        ("accounts", "0004_library_permissions"),
    ]

    operations = [migrations.RunPython(grant, migrations.RunPython.noop)]
