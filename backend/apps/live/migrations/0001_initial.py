"""Live TV and its guide (ADR-0017).

`live_epgprogram` is partitioned by month on `start` (SeparateDatabaseAndState: the
state is a plain model, the database a range-partitioned parent whose primary key
includes the partition column). `apps.core.partitions` creates and drops months;
this migration creates last month to two months ahead. The foreign key to the
guide channel cascades in the database.
"""

import apps.core.db
import apps.core.ids
import django.core.validators
import django.db.models.deletion
from django.db import migrations, models

EPG_PROGRAM_SQL = """
CREATE TABLE "live_epgprogram" (
    "id" uuid NOT NULL,
    "created_at" timestamp with time zone NOT NULL,
    "updated_at" timestamp with time zone NOT NULL,
    "start" timestamp with time zone NOT NULL,
    "stop" timestamp with time zone NOT NULL,
    "title" varchar(500) NOT NULL,
    "title_ar" varchar(500) NOT NULL,
    "description" text NOT NULL,
    "description_ar" text NOT NULL,
    "category" varchar(100) NOT NULL,
    "lang" varchar(3) NOT NULL,
    "channel_id" uuid NOT NULL
        CONSTRAINT "live_epgprogram_channel_fk" REFERENCES "live_epgchannel" ("id")
        ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT "live_epgprogram_pkey" PRIMARY KEY ("id", "start"),
    CONSTRAINT "live_epgprogram_stop_after" CHECK ("stop" > "start")
) PARTITION BY RANGE ("start");
CREATE INDEX "live_epgprogram_chan_start" ON "live_epgprogram" ("channel_id", "start");
"""


def create_partitions(apps, schema_editor):
    from django.utils import timezone

    from apps.core.partitions import add_months, ensure_monthly, month_start

    first = add_months(month_start(timezone.now()), -1)
    ensure_monthly("live_epgprogram", first, 4, using=schema_editor.connection.alias)


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('catalog', '0004_collections_search_text'),
    ]

    operations = [
        migrations.RunSQL(
            sql="CREATE SEQUENCE live_epg_source_epg_id_seq",
            reverse_sql="DROP SEQUENCE IF EXISTS live_epg_source_epg_id_seq",
        ),
        migrations.CreateModel(
            name='EpgSource',
            fields=[
                ('id', models.UUIDField(default=apps.core.ids.uuid7, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=100)),
                ('kind', models.CharField(choices=[('url', 'URL'), ('upload', 'Uploaded file')], max_length=8)),
                ('url_encrypted', models.TextField(blank=True, default='')),
                ('upload', models.BinaryField(blank=True, null=True)),
                ('upload_name', models.CharField(blank=True, max_length=255)),
                ('refresh_cron', models.CharField(default='0 */6 * * *', max_length=100)),
                ('priority', models.IntegerField(default=100)),
                ('enabled', models.BooleanField(default=True)),
                ('epg_id', models.BigIntegerField(db_default=apps.core.db.NextVal('live_epg_source_epg_id_seq'), editable=False, unique=True)),
                ('etag', models.CharField(blank=True, max_length=255)),
                ('last_modified', models.CharField(blank=True, max_length=64)),
                ('last_run_at', models.DateTimeField(blank=True, null=True)),
                ('last_ok_at', models.DateTimeField(blank=True, null=True)),
                ('last_error', models.TextField(blank=True, default='')),
                ('stats', models.JSONField(blank=True, default=dict)),
            ],
            options={
                'ordering': ('priority', 'name', 'id'),
            },
        ),
        migrations.CreateModel(
            name='LiveIntegration',
            fields=[
                ('id', models.UUIDField(default=apps.core.ids.uuid7, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('kind', models.CharField(choices=[('ersatztv', 'ErsatzTV'), ('mediamtx', 'MediaMTX')], max_length=16)),
                ('name', models.CharField(max_length=100)),
                ('base_url', models.CharField(max_length=500)),
                ('stream_base_url', models.CharField(blank=True, max_length=500)),
                ('credentials_encrypted', models.TextField(blank=True, default='')),
                ('rights_holder', models.CharField(max_length=255)),
                ('license_ref', models.CharField(blank=True, max_length=255)),
                ('last_sync_at', models.DateTimeField(blank=True, null=True)),
                ('last_error', models.TextField(blank=True, default='')),
                ('last_result', models.JSONField(blank=True, default=dict)),
            ],
            options={
                'ordering': ('name', 'id'),
            },
        ),
        migrations.CreateModel(
            name='EpgChannel',
            fields=[
                ('id', models.UUIDField(default=apps.core.ids.uuid7, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('xmltv_id', models.CharField(max_length=255)),
                ('names', models.JSONField(blank=True, default=dict)),
                ('icon_url', models.CharField(blank=True, max_length=1024)),
                ('source', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='channels', to='live.epgsource')),
            ],
            options={
                'ordering': ('xmltv_id', 'id'),
            },
        ),
        migrations.CreateModel(
            name='LiveChannel',
            fields=[
                ('id', models.UUIDField(default=apps.core.ids.uuid7, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('xc_id', models.BigIntegerField(db_default=apps.core.db.NextVal('catalog_xc_id_seq'), editable=False, unique=True)),
                ('name', models.CharField(max_length=255)),
                ('name_ar', models.CharField(blank=True, max_length=255)),
                ('sort', models.IntegerField(default=0)),
                ('epg_channel_id', models.CharField(blank=True, max_length=255, validators=[django.core.validators.RegexValidator('^[^\\s"]*$', 'No spaces or quotes.', code='invalid_epg_channel_id')])),
                ('logo', models.JSONField(blank=True, default=dict)),
                ('source_encrypted', models.TextField()),
                ('output', models.CharField(choices=[('ts', 'MPEG-TS'), ('hls', 'HLS')], default='ts', max_length=4)),
                ('transcode', models.CharField(choices=[('copy', 'Copy (remux)'), ('h264', 'Real-time H.264')], default='copy', max_length=8)),
                ('catchup_days', models.PositiveSmallIntegerField(default=0)),
                ('always_on', models.BooleanField(default=False)),
                ('enabled', models.BooleanField(default=False)),
                ('rights_holder', models.CharField(blank=True, max_length=255)),
                ('license_ref', models.CharField(blank=True, max_length=255)),
                ('license_expires_at', models.DateTimeField(blank=True, null=True)),
                ('license_lapsed', models.BooleanField(default=False)),
                ('origin', models.CharField(choices=[('manual', 'Created by an admin'), ('ersatztv', 'ErsatzTV'), ('mediamtx', 'MediaMTX')], default='manual', max_length=16)),
                ('origin_ref', models.CharField(blank=True, max_length=128)),
                ('probe', models.JSONField(blank=True, default=dict)),
                ('epg_source', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='live.epgsource')),
                ('group', models.ForeignKey(limit_choices_to={'kind': 'live'}, on_delete=django.db.models.deletion.PROTECT, related_name='live_channels', to='catalog.category')),
                ('integration', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='channels', to='live.liveintegration')),
            ],
            options={
                'ordering': ('sort', 'xc_id'),
            },
        ),
        migrations.AddField(
            model_name='epgsource',
            name='integration',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='epg_sources', to='live.liveintegration'),
        ),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name='EpgProgram',
                    fields=[
                        ('id', models.UUIDField(default=apps.core.ids.uuid7, editable=False, primary_key=True, serialize=False)),
                        ('created_at', models.DateTimeField(auto_now_add=True)),
                        ('updated_at', models.DateTimeField(auto_now=True)),
                        ('start', models.DateTimeField()),
                        ('stop', models.DateTimeField()),
                        ('title', models.CharField(max_length=500)),
                        ('title_ar', models.CharField(blank=True, max_length=500)),
                        ('description', models.TextField(blank=True)),
                        ('description_ar', models.TextField(blank=True)),
                        ('category', models.CharField(blank=True, max_length=100)),
                        ('lang', models.CharField(blank=True, max_length=3)),
                        ('channel', models.ForeignKey(on_delete=django.db.models.deletion.DO_NOTHING, related_name='programs', to='live.epgchannel')),
                    ],
                    options={
                        'ordering': ('start', 'id'),
                        'indexes': [models.Index(fields=['channel', 'start'], name='live_epgprogram_chan_start')],
                        'constraints': [models.CheckConstraint(condition=models.Q(('stop__gt', models.F('start'))), name='live_epgprogram_stop_after')],
                    },
                ),
            ],
            database_operations=[
                migrations.RunSQL(sql=EPG_PROGRAM_SQL, reverse_sql='DROP TABLE IF EXISTS "live_epgprogram" CASCADE'),
            ],
        ),
        migrations.AddIndex(
            model_name='epgchannel',
            index=models.Index(fields=['xmltv_id'], name='live_epg_channel_xmltv'),
        ),
        migrations.AddConstraint(
            model_name='epgchannel',
            constraint=models.UniqueConstraint(fields=('source', 'xmltv_id'), name='live_epg_channel_unique'),
        ),
        migrations.AddIndex(
            model_name='livechannel',
            index=models.Index(fields=['group', 'sort'], name='live_channel_group_sort'),
        ),
        migrations.AddIndex(
            model_name='livechannel',
            index=models.Index(fields=['epg_channel_id'], name='live_channel_epg_id'),
        ),
        migrations.AddConstraint(
            model_name='livechannel',
            constraint=models.UniqueConstraint(condition=models.Q(('integration__isnull', False)), fields=('integration', 'origin_ref'), name='live_channel_integration_ref'),
        ),
        migrations.AddConstraint(
            model_name='livechannel',
            constraint=models.CheckConstraint(condition=models.Q(('catchup_days__lte', 365)), name='live_channel_catchup_days'),
        ),
        migrations.AddConstraint(
            model_name='livechannel',
            constraint=models.CheckConstraint(condition=models.Q(('enabled', True), ('rights_holder', ''), _negated=True), name='live_channel_enabled_needs_rights'),
        ),
        migrations.RunSQL(
            sql="ALTER SEQUENCE live_epg_source_epg_id_seq OWNED BY live_epgsource.epg_id",
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.RunPython(create_partitions, migrations.RunPython.noop),
    ]
