from django.db import migrations

# SPEC §6 ops: the audit log is append-only. Until the app connects with a
# database role that lacks UPDATE/DELETE on this table (M14), a trigger enforces
# it for every role. TRUNCATE stays possible for the table owner: test database
# flushes and restores need it, and the M14 app role won't have it (ADR-0005).
# SQLSTATE 42501 (insufficient_privilege) is what the M14 REVOKE will raise too.
FORWARD = """
CREATE FUNCTION audit_auditlog_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit_auditlog is append-only: % is not allowed', TG_OP
        USING ERRCODE = 'insufficient_privilege';
END;
$$;

CREATE TRIGGER audit_auditlog_append_only
BEFORE UPDATE OR DELETE ON audit_auditlog
FOR EACH ROW EXECUTE FUNCTION audit_auditlog_append_only();
"""

REVERSE = """
DROP TRIGGER IF EXISTS audit_auditlog_append_only ON audit_auditlog;
DROP FUNCTION IF EXISTS audit_auditlog_append_only();
"""


class Migration(migrations.Migration):
    dependencies = [("audit", "0001_initial")]

    operations = [migrations.RunSQL(FORWARD, reverse_sql=REVERSE)]
