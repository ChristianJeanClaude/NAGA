"""Scheduler async du NAGA Scout Bot.

Planifie et exécute les jobs récurrents en parallèle avec le bot Discord :
  - TrackingJob  — tous les jours à 08h00 UTC

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
OUTREACH_POLL_INTERVAL_MINUTES = 60


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
        "Scheduler démarré — TrackingJob @08:00 UTC, OutreachPoll toutes les 60 min"
    )
    await asyncio.gather(
        tracking_loop(),
        outreach_poll_loop(),
        return_exceptions=True,
    )
