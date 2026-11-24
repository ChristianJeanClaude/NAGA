"""Accès HTTP à l'API Notion : throttle global et nouvel essai sur 429 / 5xx.

Partagé par les deux clients Notion du bot (``NotionClient`` dans discord_bot
et les fonctions de notion_leads) : la limite de débit Notion (environ 3
requêtes/s en moyenne) s'applique à l'intégration entière, pas à un client.

Les appels urllib tournent dans des threads (``asyncio.to_thread``) : le
throttle est protégé par un ``threading.Lock`` et ses attentes ne bloquent
jamais la boucle asyncio de Discord.
"""

import re
import threading
import time
import urllib.error

# Débit moyen toléré par Notion pour une intégration.
REQUESTS_PER_SECOND = 3
# Essais au total (le premier compris) avant d'abandonner une requête.
MAX_ATTEMPTS = 5
# Backoff exponentiel quand Notion ne fournit pas de Retry-After : 1, 2, 4, 8 s.
BACKOFF_BASE_SECONDS = 1.0
# Plafond d'une attente, même si Retry-After annonce plus.
MAX_DELAY_SECONDS = 60.0

# Id Notion (UUID avec ou sans tirets). Les id Discord, purement décimaux et
# plus courts, ne sont pas concernés.
_NOTION_ID_RE = re.compile(
    r"[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}"
)


def _log(message):
    print(message, flush=True)


def redact_ids(text):
    """Masque les id Notion d'un texte destiné aux logs."""
    return _NOTION_ID_RE.sub("<id>", text or "")


def is_retryable(status):
    """Vrai pour les statuts qui justifient un nouvel essai : 429 et 5xx."""
    return status == 429 or 500 <= status <= 599


def retry_delay(attempt, retry_after=None):
    """Attente (secondes) après l'échec de l'essai n° ``attempt``.

    L'en-tête Retry-After (en secondes) prime ; à défaut ou s'il est illisible,
    backoff exponentiel. Toujours plafonné à MAX_DELAY_SECONDS.
    """
    if retry_after is not None:
        try:
            seconds = float(retry_after)
        except (TypeError, ValueError):
            seconds = -1.0
        if seconds >= 0:
            return min(seconds, MAX_DELAY_SECONDS)
    return min(BACKOFF_BASE_SECONDS * 2 ** (attempt - 1), MAX_DELAY_SECONDS)


class Throttle:
    """Espace les requêtes d'au moins 1/per_second seconde, tous threads confondus."""

    def __init__(self, per_second, clock=time.monotonic, sleep=time.sleep):
        self._interval = 1.0 / per_second
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._next_slot = 0.0

    def wait(self):
        """Attend le prochain créneau libre puis le réserve.

        L'attente se fait verrou tenu : les requêtes passent une par une, dans
        l'ordre d'arrivée.
        """
        with self._lock:
            now = self._clock()
            if self._next_slot > now:
                self._sleep(self._next_slot - now)
                now = self._next_slot
            self._next_slot = now + self._interval

    def pause(self, seconds):
        """Repousse le prochain créneau : après une 429, TOUTES les requêtes attendent."""
        with self._lock:
            self._next_slot = max(self._next_slot, self._clock() + seconds)


THROTTLE = Throttle(REQUESTS_PER_SECOND)


def call_with_retry(send, label, throttle=THROTTLE, max_attempts=MAX_ATTEMPTS, log=_log):
    """Exécute ``send()`` sous throttle et le réessaie sur 429 / 5xx.

    ``label`` décrit la requête dans les logs (ex. la méthode HTTP) : jamais
    d'id Notion ni de contenu. Lève la dernière ``HTTPError`` si les essais
    sont épuisés, ou immédiatement pour un statut non réessayable (ex. 400).
    """
    for attempt in range(1, max_attempts + 1):
        throttle.wait()
        try:
            return send()
        except urllib.error.HTTPError as exc:
            if not is_retryable(exc.code) or attempt == max_attempts:
                raise
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            delay = retry_delay(attempt, retry_after)
            log(
                f"Notion {label} → HTTP {exc.code}, nouvel essai "
                f"{attempt + 1}/{max_attempts} dans {delay:.1f}s."
            )
            throttle.pause(delay)
