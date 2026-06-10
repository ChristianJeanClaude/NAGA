"""Écriture du momentum et du dernier check dans Notion."""

import logging
import os

from notion_client import AsyncClient

logger = logging.getLogger(__name__)


class NotionWriter:
    """
    Met à jour les champs de tracking dans Notion.

    Usage:
        writer = NotionWriter()
        await writer.update_game(
            page_id="...",
            momentum_score=45,
            stat_cle="+45% followers",
            lien_post="https://...",
        )
    """

    def __init__(self):
        self._client = AsyncClient(auth=os.environ.get("NOTION_TOKEN", ""))
        self._database_id = os.environ.get("NOTION_DATABASE_ID", "")

    async def update_game(
        self,
        page_id: str,
        momentum_score: int,
        stat_cle: str,              # Ex: "+45% followers (1,000 → 1,450)"
        lien_post: str,             # URL du post ou de la page Steam
    ) -> bool:
        """
        Met à jour les champs de tracking d'une fiche Notion.

        Champs mis à jour :
        - "Momentum Score" → number (momentum_score)
        - "Momentum Stat"  → rich_text (stat_cle)
        - "Momentum Post"  → url (lien_post)

        Retourne True si succès, False sinon.
        Ne raise jamais — log l'erreur et retourne False.
        """
        properties: dict = {
            "Momentum Score": {"number": momentum_score},
            "Momentum Stat": {
                "rich_text": [{"text": {"content": stat_cle}}]
            },
        }
        # url Notion n'accepte pas une chaîne vide : on n'écrit le lien que s'il
        # est présent (sinon on laisse la valeur existante intacte).
        if lien_post:
            properties["Momentum Post"] = {"url": lien_post}

        try:
            await self._client.pages.update(
                page_id=page_id, properties=properties
            )
        except Exception:
            logger.error(
                "Échec de la mise à jour du tracking Notion (page_id=%s)",
                page_id,
                exc_info=True,
            )
            return False

        return True

    async def get_all_games(self) -> list[dict]:
        """
        Retourne une liste minimale de tous les jeux en base.
        Utilisé par ScoutingJob pour la déduplication.

        Retourne une liste de dicts:
        [{"name": str, "url": str}, ...]

        Pagine automatiquement.
        Retourne [] sur toute erreur.
        """
        games: list[dict] = []
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
                    games.append(
                        {
                            "name": name,
                            "url": props.get("Steam URL", {}).get("url"),
                        }
                    )

                if response.get("has_more"):
                    cursor = response.get("next_cursor")
                else:
                    break
        except Exception:
            logger.error(
                "Échec de la lecture des jeux (writer) depuis Notion",
                exc_info=True,
            )
            return []

        return games
