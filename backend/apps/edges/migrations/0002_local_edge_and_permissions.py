"""The local edge and the Edges page's permissions (M15, ADR-0022).

- The control host's edge (`nginx-stream`, at MEDIA_BASE_URL) becomes the first edge: it
  serves every rendition from the media volume and live TV, as it did before edges
  existed. Its id is `local`, the EDGE_ID compose gives nginx-stream.
- `edges.view` and `edges.manage`: admin both, viewer the view (owners hold everything).
"""

from django.db import migrations

NEW_GRANTS = {
    "admin": ("edges.view", "edges.manage"),
    "viewer": ("edges.view",),
}


def create_local_edge(apps, schema_editor):
    Edge = apps.get_model("edges", "Edge")
    Edge.objects.get_or_create(
        edge_id="local",
        defaults={"name": "This server", "kind": "local", "storage": "disk", "weight": 100},
    )


def grant(apps, schema_editor):
    from apps.accounts.rbac import sync_rbac

    Permission = apps.get_model("accounts", "Permission")
    Role = apps.get_model("accounts", "Role")
    sync_rbac(Permission, Role)
    for role in Role.objects.filter(name__in=NEW_GRANTS):
        role.permissions.add(*Permission.objects.filter(code__in=NEW_GRANTS[role.name]))


class Migration(migrations.Migration):
    dependencies = [
        ("edges", "0001_initial"),
        ("accounts", "0005_monitoring_permission"),
    ]

    operations = [
        migrations.RunPython(create_local_edge, migrations.RunPython.noop),
        migrations.RunPython(grant, migrations.RunPython.noop),
    ]
