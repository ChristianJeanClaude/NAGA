"""Utilitaire centralisé de retry pour les coroutines.

Fournit ``with_retry``, qui exécute une coroutine et la réessaie avec un
backoff exponentiel sur les exceptions ciblées. Centralise ici la logique de
tolérance aux pannes réseau (API Steam, SteamSpy, Notion) afin que chaque
service n'ait pas à réimplémenter sa propre boucle de retry.
"""

import asyncio
import logging

logger = logging.getLogger(__name__)


async def with_retry(
    coro_func,
    *args,
    max_attempts: int = 3,
    base_delay: float = 2.0,
    exceptions: tuple = (Exception,),
    label: str = "",
    **kwargs,
):
    """Exécute une coroutine avec retry et backoff exponentiel.

    - ``max_attempts`` : nombre total de tentatives (et non de retries).
    - Délai entre tentatives : ``base_delay * 2**attempt`` (2s, 4s, 8s…).
    - Ne réessaie que sur les exceptions listées dans ``exceptions``.
    - Journalise chaque tentative échouée en WARNING (numéro + erreur).
    - Journalise en ERROR puis relance après la dernière tentative.
    - ``label`` sert de contexte dans les messages de log (ex. "Steam API").

    Les exceptions non listées dans ``exceptions`` se propagent immédiatement
    sans retry.
    """
    prefix = f"{label}: " if label else ""
    for attempt in range(max_attempts):
        try:
            return await coro_func(*args, **kwargs)
        except exceptions as exc:
            is_last = attempt == max_attempts - 1
            if is_last:
                logger.error(
                    "%séchec définitif après %d tentative(s) : %s",
                    prefix,
                    max_attempts,
                    exc,
                )
                raise
            delay = base_delay * 2 ** attempt
            logger.warning(
                "%stentative %d/%d échouée (%s). Nouvel essai dans %.0fs.",
                prefix,
                attempt + 1,
                max_attempts,
                exc,
                delay,
            )
            await asyncio.sleep(delay)
