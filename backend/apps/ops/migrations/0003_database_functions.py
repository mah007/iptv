"""Database functions for the restricted app role (G-37, ADR-0020).

The app services sign in as `iptv_app`, which may not change the schema and may not
UPDATE, DELETE or TRUNCATE audit rows. Two things they do need run through SECURITY
DEFINER functions owned by the database owner, each with its own limits:

- `audit_purge(general_days, billing_days)`: audit retention. It never deletes entries
  younger than a year, nor billing, payment and subscription entries younger than five
  years (VAT record-keeping), whatever it is asked. The append-only trigger now lets a
  DELETE through only inside this function (a transaction-local flag, checked together
  with the table owner's identity).
- `iptv_partition_create(parent, month)` and `iptv_partition_drop(parent, child)`:
  monthly partitions of the tables they list (`live_epgprogram`), named and bounded
  exactly as `apps.core.partitions` does.

PUBLIC loses EXECUTE on all three; `apps.ops.grants` grants it to `iptv_app`.
"""

from django.db import migrations

FORWARD = r"""
CREATE OR REPLACE FUNCTION audit_auditlog_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE'
       AND current_setting('iptv.audit_purge', true) = 'on'
       AND pg_has_role(current_user, (SELECT relowner FROM pg_class WHERE oid = TG_RELID), 'USAGE')
    THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION 'audit_auditlog is append-only: % is not allowed', TG_OP
        USING ERRCODE = 'insufficient_privilege';
END;
$$;

CREATE OR REPLACE FUNCTION audit_purge(general_days integer, billing_days integer)
RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    general_cutoff timestamptz := now() - make_interval(days => GREATEST(general_days, 365));
    billing_cutoff timestamptz := now() - make_interval(days => GREATEST(billing_days, 1826));
    removed bigint;
BEGIN
    PERFORM set_config('iptv.audit_purge', 'on', true);
    DELETE FROM audit_auditlog
     WHERE at < billing_cutoff
        OR (at < general_cutoff AND NOT (
                action LIKE 'billing.%' OR action LIKE 'payment.%' OR action LIKE 'plan.%'
             OR action LIKE 'subscription.%' OR action LIKE 'invoice.%'
             OR action LIKE 'checkout.%' OR action LIKE 'trial.%' OR action LIKE 'refund.%'));
    GET DIAGNOSTICS removed = ROW_COUNT;
    PERFORM set_config('iptv.audit_purge', 'off', true);
    RETURN removed;
END;
$$;
REVOKE ALL ON FUNCTION audit_purge(integer, integer) FROM PUBLIC;

CREATE OR REPLACE FUNCTION iptv_partition_create(parent text, lower_bound date)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    child text;
BEGIN
    IF NOT (parent = ANY (ARRAY['live_epgprogram'])) THEN
        RAISE EXCEPTION 'not a managed partitioned table: %', parent
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF lower_bound <> date_trunc('month', lower_bound)::date THEN
        RAISE EXCEPTION 'a monthly partition starts on the first: %', lower_bound;
    END IF;
    child := parent || '_y' || to_char(lower_bound, 'YYYY') || 'm' || to_char(lower_bound, 'MM');
    IF to_regclass(child) IS NOT NULL THEN
        RETURN false;
    END IF;
    EXECUTE format(
        'CREATE TABLE %I PARTITION OF %I FOR VALUES FROM (%L) TO (%L)',
        child, parent,
        to_char(lower_bound, 'YYYY-MM-DD') || ' 00:00:00+00',
        to_char((lower_bound + interval '1 month')::date, 'YYYY-MM-DD') || ' 00:00:00+00'
    );
    RETURN true;
END;
$$;
REVOKE ALL ON FUNCTION iptv_partition_create(text, date) FROM PUBLIC;

CREATE OR REPLACE FUNCTION iptv_partition_drop(parent text, child text)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
    IF NOT (parent = ANY (ARRAY['live_epgprogram'])) THEN
        RAISE EXCEPTION 'not a managed partitioned table: %', parent
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF child !~ ('^' || parent || '_y[0-9]{4}m[0-9]{2}$') THEN
        RAISE EXCEPTION 'not a monthly partition of %: %', parent, child
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_inherits i
        JOIN pg_class p ON p.oid = i.inhparent
        JOIN pg_class c ON c.oid = i.inhrelid
        WHERE p.relname = parent AND c.relname = child
    ) THEN
        RETURN false;
    END IF;
    EXECUTE format('DROP TABLE %I', child);
    RETURN true;
END;
$$;
REVOKE ALL ON FUNCTION iptv_partition_drop(text, text) FROM PUBLIC;
"""

REVERSE = r"""
DROP FUNCTION IF EXISTS iptv_partition_drop(text, text);
DROP FUNCTION IF EXISTS iptv_partition_create(text, date);
DROP FUNCTION IF EXISTS audit_purge(integer, integer);
CREATE OR REPLACE FUNCTION audit_auditlog_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit_auditlog is append-only: % is not allowed', TG_OP
        USING ERRCODE = 'insufficient_privilege';
END;
$$;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("ops", "0002_backup_permissions"),
        ("audit", "0002_append_only"),
        ("live", "0002_integration_group"),
    ]

    operations = [migrations.RunSQL(FORWARD, reverse_sql=REVERSE)]
