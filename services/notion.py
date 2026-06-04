"""Service d'intégration avec l'API Notion.

Gère toutes les interactions avec la base Notion via le client asynchrone
officiel ``notion-client`` : recherche d'une fiche existante par App ID Steam
(déduplication) et création d'une nouvelle fiche à partir d'un ``GameData``.

Les erreurs de l'API Notion sont attrapées et journalisées : la recherche
échoue « en douceur » (retourne ``None``) pour ne pas bloquer le bot, tandis
que la création lève une ``RuntimeError`` explicite afin que l'appelant sache
que la fiche n'a pas pu être enregistrée.
"""

import logging

from notion_client import AsyncClient

from config import NOTION_DATABASE_ID, NOTION_TOKEN
from models.game import GameData
from services.retry import with_retry

logger = logging.getLogger(__name__)

client = AsyncClient(auth=NOTION_TOKEN)


async def find_existing_page(app_id: int) -> str | None:
    """Cherche une fiche existante pour cet App ID Steam.

    Retourne l'URL de la page Notion si trouvée, ``None`` sinon. En cas
    d'erreur de l'API Notion, journalise et retourne ``None`` (le bot ne
    plante pas).
    """
    try:
        response = await client.databases.query(
            database_id=str(NOTION_DATABASE_ID),
            filter={"property": "Steam App ID", "number": {"equals": app_id}},
        )
    except Exception:
        logger.error(
            "Échec de la recherche Notion pour app_id=%s", app_id, exc_info=True
        )
        return None

    results = response.get("results", [])
    if not results:
        return None

    url = results[0].get("url")
    logger.warning(
        "Fiche déjà existante pour app_id=%s : %s", app_id, url
    )
    return url


async def get_page_id(app_id: int) -> str | None:
    """Comme ``find_existing_page`` mais retourne l'ID de page plutôt que l'URL.

    Retourne ``page["id"]`` si une fiche existe pour cet App ID, ``None`` sinon
    (y compris en cas d'erreur de l'API Notion, journalisée sans planter).
    """
    try:
        response = await client.databases.query(
            database_id=str(NOTION_DATABASE_ID),
            filter={"property": "Steam App ID", "number": {"equals": app_id}},
        )
    except Exception:
        logger.error(
            "Échec de la recherche Notion (page_id) pour app_id=%s",
            app_id,
            exc_info=True,
        )
        return None

    results = response.get("results", [])
    if not results:
        return None

    return results[0].get("id")


async def get_all_app_ids() -> set[int]:
    """Retourne tous les App IDs Steam présents dans la base Notion.

    Sert à exclure les jeux déjà scoutés des suggestions. Pagine sur tous les
    résultats. Retourne un ensemble vide en cas d'erreur.
    """
    app_ids: set[int] = set()
    try:
        cursor = None
        while True:
            kwargs = {"database_id": str(NOTION_DATABASE_ID), "page_size": 100}
            if cursor:
                kwargs["start_cursor"] = cursor
            response = await client.databases.query(**kwargs)

            for page in response.get("results", []):
                prop = page.get("properties", {}).get("Steam App ID", {})
                number = prop.get("number")
                if number is not None:
                    app_ids.add(int(number))

            if response.get("has_more"):
                cursor = response.get("next_cursor")
            else:
                break
    except Exception:
        logger.error(
            "Échec de la récupération des App IDs depuis Notion", exc_info=True
        )
        return set()

    return app_ids


async def create_game_page(game: GameData) -> str:
    """Crée une nouvelle fiche Notion à partir d'un ``GameData``.

    Construit les propriétés via ``game.to_notion_properties()`` et force le
    statut « Scouted ». Retourne l'URL de la page créée. Lève une
    ``RuntimeError`` explicite en cas d'échec.
    """
    properties = game.to_notion_properties()
    properties["Status"] = {"select": {"name": "Scouted"}}

    try:
        response = await with_retry(
            client.pages.create,
            parent={"database_id": NOTION_DATABASE_ID},
            properties=properties,
            exceptions=(Exception,),
            max_attempts=3,
            label="Notion create",
        )
    except Exception as exc:
        raise RuntimeError(
            f"Failed to create Notion page for app_id={game.app_id}"
        ) from exc

    url = response["url"]
    logger.info(
        "Fiche Notion créée pour '%s' (app_id=%s) : %s",
        game.name,
        game.app_id,
        url,
    )
    return url
