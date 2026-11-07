import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from . import config
from .sync import SyncBusy, run_sync

log = logging.getLogger("scheduler")


def start_scheduler(sf, sync_time: str | None = None) -> BackgroundScheduler:
    hh, mm = (sync_time or config.SYNC_TIME).split(":")

    def job():
        try:
            run_sync(sf, trigger="scheduled")
        except SyncBusy:
            log.warning("scheduled sync skipped: another run in progress")

    sched = BackgroundScheduler(timezone=config.TIMEZONE)
    sched.add_job(job, CronTrigger(hour=int(hh), minute=int(mm), timezone=config.TIMEZONE),
                  id="daily_sync", coalesce=True, misfire_grace_time=3600)
    sched.start()
    return sched
