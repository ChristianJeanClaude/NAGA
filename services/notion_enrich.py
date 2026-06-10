"""Enrichissement d'une fiche Notion juste après sa création.

Complète une fiche existante avec des données secondaires collectées depuis
Steam et SteamSpy (captures d'écran, bande-annonce, abonnés, date de sortie).
L'enrichissement est « best-effort » : toute erreur est journalisée et avalée
pour ne jamais faire échouer le pipeline de scouting principal.
"""

import logging

from models.game import GameData
from services.notion import client
from services.retry import with_retry

logger = logging.getLogger(__name__)

# Seules ces propriétés sont (ré)écrites par l'enrichissement.
ENRICH_KEYS = {"Trailer", "Followers", "Release Date"}


async def enrich_game_page(page_id: str, game: GameData) -> None:
    """Met à jour une fiche Notion avec les données d'enrichissement.

    Pousse uniquement ``Trailer``, ``Followers`` et ``Release Date`` (les
    champs absents sont naturellement omis par
    ``to_notion_properties``). N'a aucun effet si aucune de ces propriétés
    n'est disponible. Les erreurs de l'API Notion sont journalisées sans être
    propagées : l'enrichissement ne doit jamais faire échouer le scouting.
    """
    all_props = game.to_notion_properties()
    properties = {k: v for k, v in all_props.items() if k in ENRICH_KEYS}

    if not properties:
        return

    try:
        await with_retry(
            client.pages.update,
            page_id=page_id,
            properties=properties,
            max_attempts=3,
            label="Notion enrich",
        )
    except Exception:
        logger.error(
            "Échec de l'enrichissement de la fiche Notion pour app_id=%s",
            game.app_id,
            exc_info=True,
        )
