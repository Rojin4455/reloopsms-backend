# Celery reliability on EC2

Periodic tasks use **django-celery-beat** (`DatabaseScheduler`). Three layers prevent missed runs:

1. **Beat + DatabaseScheduler** — primary scheduler (Postgres-backed)
2. **Hourly watchdog** — `ensure_periodic_tasks` re-queues anything Beat missed
3. **OAuth cron backup** — `refresh_oauth_tokens` every 6h (critical for token expiry)

## Services

Copy or diff against `/etc/systemd/system/`:

```bash
sudo cp deploy/systemd/reloop-celerybeat.service /etc/systemd/system/
sudo cp deploy/systemd/reloop-celery.service /etc/systemd/system/
sudo cp deploy/systemd/reloop-celery-critical.service /etc/systemd/system/
sudo cp deploy/systemd/reloop-celery-outbound.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable reloop-celery-outbound
sudo systemctl restart reloop-celerybeat reloop-celery reloop-celery-critical reloop-celery-outbound
```

Beat **must** include `-S django`. All services use `Restart=always`.

### Queue layout (t3.small)

| Service | Queue | Concurrency | Purpose |
|---------|-------|-------------|---------|
| `reloop-celery-critical` | `critical` | 2 | OAuth token refresh only |
| `reloop-celery-outbound` | `outbound` | 2 @ 8/s | Campaign SMS → Transmit |
| `reloop-celery` | `celery` | 2 | Inbound, GHL sync, daily jobs, bulk retry enqueue |

Do **not** put `critical` on the general worker — OAuth must stay isolated.

## Cron (watchdog + OAuth backup)

```bash
crontab -e
```

Add the lines from `deploy/cron/reloop-celery.cron`, or:

```bash
(crontab -l 2>/dev/null; cat deploy/cron/reloop-celery.cron | grep -v '^#') | crontab -
```

Create log files if needed:

```bash
sudo touch /var/log/reloop-periodic-watchdog.log /var/log/reloop-oauth-backup.log
sudo chown ubuntu:ubuntu /var/log/reloop-periodic-watchdog.log /var/log/reloop-oauth-backup.log
```

## Verify

```bash
# Periodic tasks in DB
python manage.py shell -c "from django_celery_beat.models import PeriodicTask; print(PeriodicTask.objects.count())"

# Watchdog dry-run
python manage.py ensure_periodic_tasks --dry-run

# Beat logs
sudo journalctl -u reloop-celerybeat -n 30 --no-pager
```

## Schedule (UTC)

| Task | When |
|------|------|
| OAuth location refresh | :00 at 0, 10, 20 |
| OAuth agency refresh | :15 at 0, 10, 20 |
| Wallet custom fields sync | :25 at 0, 10, 20 |
| Charge due numbers | daily 00:00 |
| Sync client-owned numbers | daily 00:30 |

Edit schedules in Django admin → Periodic Tasks (no redeploy required).
