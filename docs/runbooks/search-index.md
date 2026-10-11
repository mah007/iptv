# Runbook: rebuild the search index

Meilisearch holds a copy of the catalogue for search: titles in Arabic and English, typo tolerance, and filters. Nothing in it is original data, so it is never backed up; it is rebuilt from PostgreSQL. While it's down, search falls back to PostgreSQL: slower and less tolerant of typos, but it works.

```bash
cd /opt/iptv
alias dc='docker compose --project-directory . -f docker/compose.yml -f docker/compose.prod.yml'   # plus your overlays
```

## When to rebuild
It rebuilds itself nightly at 03:10, and changes to titles and categories are indexed as they happen. Rebuild by hand:
- after restoring the database ([backups.md](backups.md));
- after Meilisearch lost its volume or was upgraded;
- when search misses titles that exist, or shows deleted ones;
- after a MeilisearchUnhealthy alert ([alerts.md](alerts.md#meilisearchunhealthy)), once it's healthy again;
- after rotating `MEILI_MASTER_KEY` ([rotate-keys.md](rotate-keys.md)).

## Rebuild
```bash
dc exec web python manage.py search_reindex
# Search index rebuilt: 412 movie, 57 series.
```
- The new index is built beside the live one and swapped in atomically, so search keeps answering throughout.
- It takes seconds for a few thousand titles.
- Running it twice at once is harmless: the last swap wins.

## When Meilisearch won't start
1. Look: `dc logs --tail 100 meilisearch`, and `df -h` (a full disk is the usual cause).
2. Restart it: `dc up -d --wait meilisearch`, then rebuild.
3. **A corrupt database, or an upgrade that refuses the old data format:**
   - the data can be thrown away: stop Meilisearch, remove its volume (`docker volume rm <project>_meili-data`; check the exact name with `docker volume ls`), start it, and rebuild;
   - it's the only volume that is safe to delete like this, and still: be sure it's the Meilisearch one.

## Check
- The portal's search finds a title by part of its name in Arabic and in English.
- `dc exec web python manage.py shell -c "from apps.search import meili; print(meili.client().healthy())"` prints `True`.
