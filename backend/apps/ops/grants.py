"""Database roles and their privileges (G-37, ADR-0020).

Three roles share the database:

- the **owner** (`POSTGRES_USER`, the image's bootstrap superuser) owns every object.
  Only one-shot services sign in as it: `db-roles`, `migrate`, the monitoring role's
  init. It is the migrator.
- **`iptv_app`**, what web, the workers, beat, the watcher, the transcoder and the live
  packager sign in as: read and write rows, use sequences, and nothing else. No DDL, and
  no UPDATE, DELETE or TRUNCATE on the audit log (audit retention goes through
  `audit_purge`, which enforces its own floors). Monthly partitions are created and
  dropped through `iptv_partition_create` and `iptv_partition_drop` (SECURITY DEFINER,
  limited to the tables listed there).
- **`iptv_reporting`**, read-only for the operator's reports: every table except those
  that hold credentials, sessions or raw provider payloads.

The `db-roles` service (docker/postgres/db-roles.sh) creates the roles and sets their
passwords before `migrate` runs. `apply` runs after every `migrate` (post_migrate), in
every database, test databases included: it grants on what exists and sets the default
privileges for what the owner creates later. Both are idempotent, so a server that
predates the roles upgrades in place.
"""

import logging
from typing import Any, Final

from django.db.backends.base.base import BaseDatabaseWrapper

logger = logging.getLogger(__name__)

APP_ROLE: Final = "iptv_app"
REPORTING_ROLE: Final = "iptv_reporting"
AUDIT_TABLE: Final = "audit_auditlog"
#: SECURITY DEFINER functions the app may call (created by ops migration 0003).
APP_FUNCTIONS: Final = (
    "audit_purge(integer, integer)",
    "iptv_partition_create(text, date)",
    "iptv_partition_drop(text, text)",
)
#: Tables the reporting role may not read.
REPORTING_HIDDEN: Final = (
    "accounts_xtreamcredential",
    "accounts_mfatotp",
    "django_session",
    "axes_accessattempt",
    "axes_accessfailurelog",
    "axes_accesslog",
    "core_setting",
    "live_liveintegration",
    "billing_webhookevent",
)


def _existing_roles(cursor: Any) -> set[str]:
    cursor.execute(
        "SELECT rolname FROM pg_roles WHERE rolname = ANY(%s)", [[APP_ROLE, REPORTING_ROLE]]
    )
    return {row[0] for row in cursor.fetchall()}


def _existing_tables(cursor: Any, names: tuple[str, ...]) -> list[str]:
    cursor.execute(
        "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relname = ANY(%s)",
        [list(names)],
    )
    return sorted(row[0] for row in cursor.fetchall())


def _existing_functions(cursor: Any) -> list[str]:
    found = []
    for signature in APP_FUNCTIONS:
        cursor.execute("SELECT to_regprocedure(%s) IS NOT NULL", [signature])
        row = cursor.fetchone()
        if row and row[0]:
            found.append(signature)
    return found


def statements(  # noqa: PLR0913 (keyword-only facts about the database)
    *,
    database: str,
    owner: str,
    roles: set[str],
    audit_exists: bool,
    functions: list[str],
    hidden: list[str],
    quote: Any,
) -> list[str]:
    """The GRANT/REVOKE statements for the roles that exist (pure; tested on its own)."""
    sql: list[str] = []
    q_owner = quote(owner)
    if APP_ROLE in roles:
        app = quote(APP_ROLE)
        sql += [
            f"GRANT CONNECT ON DATABASE {quote(database)} TO {app}",
            f"GRANT USAGE ON SCHEMA public TO {app}",
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {app}",
            f"GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO {app}",
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {q_owner} IN SCHEMA public "
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {app}",
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {q_owner} IN SCHEMA public "
            f"GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO {app}",
            f"REVOKE CREATE ON SCHEMA public FROM {app}",
        ]
        if audit_exists:
            sql.append(f"REVOKE UPDATE, DELETE, TRUNCATE ON {quote(AUDIT_TABLE)} FROM {app}")
        sql += [f"GRANT EXECUTE ON FUNCTION {signature} TO {app}" for signature in functions]
    if REPORTING_ROLE in roles:
        reporting = quote(REPORTING_ROLE)
        sql += [
            f"GRANT CONNECT ON DATABASE {quote(database)} TO {reporting}",
            f"GRANT USAGE ON SCHEMA public TO {reporting}",
            f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {reporting}",
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {q_owner} IN SCHEMA public "
            f"GRANT SELECT ON TABLES TO {reporting}",
            f"REVOKE CREATE ON SCHEMA public FROM {reporting}",
        ]
        # Identifiers quoted by the connection: no injection possible.
        sql += [f"REVOKE SELECT ON {quote(table)} FROM {reporting}" for table in hidden]  # noqa: S608
    return sql


def apply(connection: BaseDatabaseWrapper) -> list[str]:
    """Grant what the roles need in this database; returns the statements run."""
    if connection.vendor != "postgresql":
        return []
    with connection.cursor() as cursor:
        roles = _existing_roles(cursor)
        if not roles:
            return []
        cursor.execute("SELECT current_database(), current_user")
        database, owner = cursor.fetchone()
        sql = statements(
            database=database,
            owner=owner,
            roles=roles,
            audit_exists=bool(_existing_tables(cursor, (AUDIT_TABLE,))),
            functions=_existing_functions(cursor),
            hidden=_existing_tables(cursor, REPORTING_HIDDEN),
            quote=connection.ops.quote_name,
        )
        for statement in sql:
            cursor.execute(statement)
    logger.info("ops.grants", extra={"roles": sorted(roles), "statements": len(sql)})
    return sql


def owns_schema(connection: BaseDatabaseWrapper) -> bool:
    """True when this connection may change the schema (it owns the migrations table, or
    no table exists yet)."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_has_role(current_user, c.relowner, 'USAGE') FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relname = 'django_migrations'"
        )
        row = cursor.fetchone()
    return row is None or bool(row[0])
