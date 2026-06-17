"""Scheduler async du NAGA Scout Bot.

Planifie et exécute les jobs récurrents en parallèle avec le bot Discord :
  - TrackingJob  — tous les jours à 08h00 UTC
  - ScoutingJob  — tous les 2 jours à 10h00 UTC

Les jobs tournent dans la même boucle asyncio que le bot Discord.
Toute erreur d'un job est attrapée et loggée — un crash ne stoppe
jamais le scheduler.
"""

import asyncio
import logging
from datetime import datetime, time as dt_time, timedelta, timezone

logger = logging.getLogger(__name__)

# Horaires de déclenchement (UTC) et cadence des jobs.
_TRACKING_TIME = dt_time(hour=8, minute=0)
_SCOUTING_TIME = dt_time(hour=10, minute=0)
_SCOUTING_INTERVAL_DAYS = 2
OUTREACH_POLL_INTERVAL_MINUTES = 60


async def _should_run_today(last_run: datetime | None, interval_days: int) -> bool:
    """
    Retourne True si le job doit tourner aujourd'hui.
    - Si jamais tourné (last_run=None) → True
    - Si dernier run il y a >= interval_days → True
    """
    if last_run is None:
        return True
    return datetime.now(timezone.utc) - last_run >= timedelta(days=interval_days)


async def _wait_until(target_time: dt_time) -> None:
    """
    Attend jusqu'à la prochaine occurrence de target_time (UTC).
    Si target_time est déjà passé aujourd'hui → attend demain.
    """
    now = datetime.now(timezone.utc)
    target = datetime.combine(now.date(), target_time, tzinfo=timezone.utc)
    if target <= now:
        target += timedelta(days=1)
    await asyncio.sleep((target - now).total_seconds())


async def run_tracking_job() -> None:
    """
    Lance le TrackingJob.
    Import lazy pour éviter les imports circulaires.
    Loggue début, fin et erreurs.
    Ne raise jamais.
    """
    from tracking.job import TrackingJob
    logger.info("TrackingJob — démarrage")
    try:
        job = TrackingJob()
        await job.run()
        logger.info("TrackingJob — terminé")
    except Exception as exc:
        logger.error(f"TrackingJob — ÉCHEC : {exc}", exc_info=True)
    finally:
        try:
            job.close()
        except Exception:
            pass


async def run_scouting_job() -> None:
    """
    Lance le ScoutingJob.
    Import lazy pour éviter les imports circulaires.
    Loggue début, fin et erreurs.
    Ne raise jamais.
    """
    from scouting.job import ScoutingJob
    logger.info("ScoutingJob — démarrage")
    try:
        job = ScoutingJob()
        await job.run()
        logger.info("ScoutingJob — terminé")
    except Exception as exc:
        logger.error(f"ScoutingJob — ÉCHEC : {exc}", exc_info=True)
    finally:
        try:
            job.close()
        except Exception:
            pass


async def tracking_loop() -> None:
    """
    Boucle du TrackingJob :
    - Attend 08h00 UTC
    - Lance run_tracking_job()
    - Répète chaque jour
    """
    while True:
        await _wait_until(_TRACKING_TIME)
        await run_tracking_job()


async def scouting_loop() -> None:
    """
    Boucle du ScoutingJob :
    - Attend 10h00 UTC
    - Lance run_scouting_job()
    - Répète tous les 2 jours
    """
    last_run: datetime | None = None
    while True:
        await _wait_until(_SCOUTING_TIME)
        if await _should_run_today(last_run, _SCOUTING_INTERVAL_DAYS):
            await run_scouting_job()
            last_run = datetime.now(timezone.utc)


async def outreach_poll_loop() -> None:
    """
    Boucle de polling outreach :
    - Lance run_outreach_poll()
    - Répète toutes les 60 minutes
    """
    from tracking.outreach_poller import run_outreach_poll
    while True:
        await run_outreach_poll()
        await asyncio.sleep(OUTREACH_POLL_INTERVAL_MINUTES * 60)


async def start_scheduler() -> None:
    """
    Lance les deux boucles en tâches asyncio parallèles.
    Appelé depuis main.py au démarrage du bot.

    Usage in main.py:
        asyncio.create_task(start_scheduler())
    """
    logger.info(
        "Scheduler démarré — TrackingJob @08:00 UTC, ScoutingJob @10:00 UTC (J+2), "
        "OutreachPoll toutes les 60 min"
    )
    await asyncio.gather(
        tracking_loop(),
        scouting_loop(),
        outreach_poll_loop(),
        return_exceptions=True,
    )
