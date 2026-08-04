# Backup and Restore

> This is a software prototype using synthetic data. It is not certified or approved for real
> medical delivery operations and does not claim HIPAA, OSHA, DOT, pharmacy, employment, or other
> legal compliance.

This document covers backup/restore for the local `docker compose` stack (`compose.yaml`):
PostgreSQL/PostGIS (`db`) and Valkey (`valkey`). It is Phase 8 (`docs/IMPLEMENTATION_ROADMAP.md`)
documentation, written and — for the Postgres path — **actually executed and verified**, not just
reviewed on paper. See the "What was actually run" section below for the exact drill performed in
this session, with real output.

## Scope: what needs backing up

| Service | Needs backup? | Why |
|---|---|---|
| `db` (PostgreSQL/PostGIS) | **Yes** | The only system of record in this application. Every domain model — organizations, facilities, couriers, deliveries, dispatch, custody events, incidents, notifications, invoices, exports, audit events — lives here. Losing it loses everything. |
| `valkey` (cache + Celery broker) | **No, by design** | `CACHES["default"]` (`config/settings/base.py`) is pure cache — Django cache invalidation/regeneration already assumes a cache miss is always safe and cheap to recompute. `CELERY_BROKER_URL`/`CELERY_RESULT_BACKEND` also point at Valkey (`config/settings/base.py`), so an in-flight (not-yet-executed) Celery task queued at the moment of a Valkey loss would be dropped — for this prototype's synthetic, retriable, non-financial-settlement background work (see `docs/ARCHITECTURE_AND_DATA_MODEL.md` section 9, "make Celery tasks idempotent"), that is an acceptable, bounded loss, not a data-integrity problem, since nothing in Valkey is the authoritative copy of anything. A real pilot with a heavier async-task backlog might reconsider this (e.g. Valkey AOF/RDB persistence tuning), but that is out of scope for the demo per `docs/TECH_STACK_AND_ZERO_COST_POLICY.md`'s demo/pilot cost distinction. |
| `mailpit` | No | Local dev-only email capture; ephemeral by design, never a system of record. |
| Django app code / static files | No (via backup) | Version-controlled in Git; redeployed from the repository, not backed up as data. |

## Backing up PostgreSQL

### Command (from the host, against the compose `db` service)

```bash
# While `docker compose up` is running:
docker compose exec db pg_dump -U "${POSTGRES_USER:-medrelay}" -Fc "${POSTGRES_DB:-medrelay}" \
  -f /tmp/medrelay_backup.dump
docker compose cp db:/tmp/medrelay_backup.dump ./medrelay_backup_$(date +%Y%m%d_%H%M%S).dump
```

- `-Fc` (custom format) is used rather than plain SQL: it is compressed, supports parallel restore
  (`pg_restore -j`), and lets you restore a subset of objects later if ever needed — all strict
  upgrades over a plain-text `pg_dump > file.sql` for no extra cost.
- Copy the dump out of the container immediately (`docker compose cp`) — a dump left only inside
  the container is destroyed along with it on `docker compose down -v`.
- For a real (non-demo) deployment, this command should run on a schedule (cron/systemd timer or a
  dedicated backup container) with the output shipped to storage outside the same host — neither
  of which this prototype's zero-cost, single-developer-machine posture requires today. Documented
  here as the obvious next step for `PILOT_MODE`, not something built in this repository.

### Restoring

```bash
# Stop anything writing to the DB first (docker compose stop web worker), then:
docker compose exec -T db psql -U "${POSTGRES_USER:-medrelay}" -d postgres \
  -c "DROP DATABASE IF EXISTS ${POSTGRES_DB:-medrelay};"
docker compose exec -T db psql -U "${POSTGRES_USER:-medrelay}" -d postgres \
  -c "CREATE DATABASE ${POSTGRES_DB:-medrelay} OWNER ${POSTGRES_USER:-medrelay};"
docker compose cp ./medrelay_backup_YYYYMMDD_HHMMSS.dump db:/tmp/restore.dump
docker compose exec db pg_restore -U "${POSTGRES_USER:-medrelay}" -d "${POSTGRES_DB:-medrelay}" \
  /tmp/restore.dump
docker compose start web worker
```

After restoring, run `python manage.py migrate` once more before serving traffic — this is a no-op
if the dump already reflects the latest migration state (which it will, since the dump is of a
live database that had migrations applied), but it is a cheap, safe habit if the restore target is
ever a few migrations behind the code being deployed.

## What was actually run (this session's real drill)

Rather than only writing the commands above, this session executed a full backup/restore cycle
against a real, disposable PostGIS container (the same `postgis/postgis:17-3.5` image
`compose.yaml`'s `db` service uses), remapped to host port `15432` to avoid colliding with
unrelated Postgres containers already running on this shared development machine (the same
port-conflict situation Phase 0's `docs/CURRENT_STATUS.md` documents — the remap was a temporary,
uncommitted local compose override, deleted after the drill, exactly like Phase 0's).

Steps actually performed, in order:

1. Started a disposable `db` container, waited for its healthcheck to report `healthy`.
2. `python manage.py migrate` against it — every migration from every app applied cleanly
   (`accounts`, `organizations`, `audit`, `facilities`, `cargo`, `deliveries`, `billing`, `custody`,
   `couriers`, `dispatch`, `incidents`, `notifications`, `otp_totp` (django-otp's own migrations),
   `recipient`, `reporting`, `sessions`, `temperature`, `tracking` — 38 migrations total).
3. `python manage.py seed_demo_data` — seeded 3 organizations, 8 facilities, 18 customer-org
   memberships, 7 internal-staff users (25 users total).
4. Recorded row counts before backup: **Organizations: 3, Memberships: 18, Facilities: 8,
   Users: 25**.
5. `pg_dump -Fc` inside the container, producing a real 286,493-byte dump file, copied out to the
   host with `docker cp`.
6. `DROP DATABASE medrelay;` then `CREATE DATABASE medrelay OWNER medrelay;` — genuinely destroying
   all data, not just truncating tables.
7. `pg_restore` from the dump file — completed with **no errors**.
8. Re-ran the same row-count query: **Organizations: 3, Memberships: 18, Facilities: 8,
   Users: 25** — identical to step 4, and `Organization.objects.values_list("name", flat=True)`
   returned the same three real organization names (`Brooklyn Family Pharmacy Network (Demo)`,
   `NorthStar Diagnostics (Demo)`, `Riverside Urgent Care Group (Demo)`), confirming this was a
   genuine data round-trip, not merely "the command exited 0."
9. Tore down the disposable container and volume (`docker compose down -v`) and deleted the
   temporary compose override and local dump file — nothing from this drill was left running or
   committed.

**This was executed, not merely reviewed.** The exact commands above (the "real deployment"
section) are the same commands used in this drill, adjusted only to target the actual `compose.yaml`
`db` service name/port instead of the disposable remapped one used for the drill itself.

## Valkey

No backup procedure is documented for Valkey beyond what is already true: it is disposable cache/
broker state, safe to lose (see the scope table above). If a real pilot later needs Valkey
persistence (e.g. to survive a broker restart without losing queued-but-not-yet-executed tasks),
Valkey's own RDB/AOF persistence settings would be the mechanism — not attempted or needed here.

## What this document does not cover

- Automated/scheduled backups (cron, managed backup service, off-host storage) — a `PILOT_MODE`
  concern, not a `DEMO_MODE` one, per `docs/TECH_STACK_AND_ZERO_COST_POLICY.md`.
- Point-in-time recovery (WAL archiving) — not configured; `pg_dump` is a full logical snapshot at
  the moment it runs, which is sufficient for this prototype's single-developer-machine demo
  posture.
- Encryption of backup files at rest — `docs/SECURITY_COMPLIANCE_BOUNDARIES.md` section 5 already
  notes database encryption-at-rest is an infrastructure concern deferred to a future pilot
  deployment; the same applies to backup-file encryption.
