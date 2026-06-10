"""Lecture de la base Notion pour le tracking hebdomadaire."""

import logging
import os
import re
from dataclasses import dataclass

from dotenv import load_dotenv
from notion_client import AsyncClient

load_dotenv()

logger = logging.getLogger(__name__)

# Extrait le handle depuis une URL Twitter/X : capture le 1er segment de chemin
# après le domaine (twitter.com ou x.com), en ignorant query string et fragment.
_TWITTER_HANDLE_RE = re.compile(
    r"(?:twitter\.com|x\.com)/(?:#!/)?@?([^/?#]+)", re.IGNORECASE
)


def _extract_twitter_handle(url: str | None) -> str | None:
    """Extrait le handle ``@NomStudio`` depuis une URL Twitter/X.

    ``"https://x.com/NomStudio"`` → ``"@NomStudio"``. Retourne ``None`` si
    l'URL est absente ou ne correspond pas à une URL Twitter/X exploitable.
    """
    if not url:
        return None
    match = _TWITTER_HANDLE_RE.search(url)
    if not match:
        return None
    handle = match.group(1).strip()
    if not handle:
        return None
    return f"@{handle}"


@dataclass
class TrackedGame:
    page_id: str           # ID de la page Notion
    name: str              # Nom du jeu
    steam_app_id: int | None
    twitter_handle: str | None  # Ex: "@NomDuStudio"
    steam_url: str | None


class NotionReader:
    """
    Lit les jeux de la base Notion pour le job de tracking.

    Usage:
        reader = NotionReader()
        games = reader.get_games_to_track_today(day_index=0)
    """

    def __init__(self):
        self._client = AsyncClient(auth=os.environ.get("NOTION_TOKEN", ""))
        self._database_id = os.environ.get("NOTION_DATABASE_ID", "")

    async def get_all_games(self) -> list[TrackedGame]:
        """
        Retourne tous les jeux de la base Notion.
        Pagine automatiquement (max 100 par appel Notion).
        Retourne [] sur toute erreur — ne raise jamais.

        Mappe ces propriétés Notion → TrackedGame:
        - "Game" (title) → name
        - "Steam App ID" (number) → steam_app_id
        - "Twitter URL" (url) → twitter_handle
          (extraire le handle depuis l'URL, ex:
           "https://x.com/NomStudio" → "@NomStudio")
        - "Steam URL" (url) → steam_url
        """
        games: list[TrackedGame] = []
        try:
            cursor = None
            while True:
                kwargs = {"database_id": str(self._database_id), "page_size": 100}
                if cursor:
                    kwargs["start_cursor"] = cursor
                response = await self._client.databases.query(**kwargs)

                for page in response.get("results", []):
                    props = page.get("properties", {})

                    title_parts = props.get("Game", {}).get("title", [])
                    name = "".join(
                        part.get("plain_text", "") for part in title_parts
                    )

                    steam_app_id = props.get("Steam App ID", {}).get("number")
                    if steam_app_id is not None:
                        steam_app_id = int(steam_app_id)

                    games.append(
                        TrackedGame(
                            page_id=page.get("id", ""),
                            name=name,
                            steam_app_id=steam_app_id,
                            twitter_handle=_extract_twitter_handle(
                                props.get("Twitter URL", {}).get("url")
                            ),
                            steam_url=props.get("Steam URL", {}).get("url"),
                        )
                    )

                if response.get("has_more"):
                    cursor = response.get("next_cursor")
                else:
                    break
        except Exception:
            logger.error(
                "Échec de la lecture des jeux depuis Notion", exc_info=True
            )
            return []

        return games

    async def get_games_to_track_today(self, day_index: int) -> list[TrackedGame]:
        """
        Retourne 1/7 des jeux à vérifier aujourd'hui.

        day_index: 0=lundi … 6=dimanche (datetime.weekday())

        Algorithme :
        - Récupère tous les jeux via get_all_games()
        - Trie par page_id (stable, reproductible)
        - Prend les jeux dont index % 7 == day_index
        - Retourne cette tranche
        """
        games = await self.get_all_games()
        games.sort(key=lambda game: game.page_id)
        return [
            game for index, game in enumerate(games) if index % 7 == day_index
        ]
