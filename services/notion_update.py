"""Mise à jour d'une fiche Notion existante avec des données fraîches.

Sépare la logique de mise à jour (commande ``!rescan``) de la création
initiale (``services.notion``). Seules les métriques susceptibles d'évoluer
sont réécrites ; les champs d'identité (Game, Steam App ID, Steam URL,
Scouted By) et le statut sont préservés.
"""

import logging

from models.game import GameData
from services.notion import client
from services.retry import with_retry

logger = logging.getLogger(__name__)

# Champs jamais réécrits lors d'un rescan : identité (dont date/auteur de
# scouting) + statut + données d'enrichissement (gérées par notion_enrich).
EXCLUDED = {
    "Game",
    "Steam App ID",
    "Steam URL",
    "Scouted By",
    "Scouted At",
    "Status",
    "Trailer",
    "Followers",
    "Release Date",
}


async def update_game_page(page_id: str, game: GameData) -> str:
    """Met à jour une fiche Notion existante avec un ``GameData`` rafraîchi.

    Ne met à jour que les champs susceptibles d'évoluer (Description, Developer,
    Genres, Tags, Website, Review Score, Review Count, Owners Estimate, Peak
    CCU, Twitter URL, Discord URL). Ne touche jamais à Name, Status, Scouted By,
    Steam App ID ni Steam URL.

    Enveloppe l'appel dans ``with_retry`` (label="Notion update"). Retourne
    l'URL de la page. Lève une ``RuntimeError`` explicite en cas d'échec.
    """
    all_props = game.to_notion_properties()
    properties = {k: v for k, v in all_props.items() if k not in EXCLUDED}

    try:
        response = await with_retry(
            client.pages.update,
            page_id=page_id,
            properties=properties,
            max_attempts=3,
            label="Notion update",
        )
    except Exception as exc:
        raise RuntimeError(
            f"Failed to update Notion page for app_id={game.app_id}"
        ) from exc

    return response["url"]
