"""Suggestions de jeux à scouter (commande ``!suggest``).

Construit un « profil NAGA » à partir de la base Notion (genres et tags les
plus fréquents), puis interroge la recherche du Steam Store pour proposer des
jeux indés à venir, en excluant ceux déjà scoutés.

Les deux fonctions sont défensives : ``get_naga_profile`` retourne un profil
vide en cas d'erreur Notion, et ``search_steam_suggestions`` retourne une liste
vide sur n'importe quelle erreur (jamais d'exception propagée).
"""

import logging
import random
import re
from collections import Counter

import aiohttp

from services.steam import STEAM_API_URL, STEAM_STORE_URL, STEAMSPY_API_URL

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://store.steampowered.com/search/results/"
# Les items de recherche exposent l'App ID dans l'URL du logo (.../apps/<id>/...).
_LOGO_APPID_RE = re.compile(r"/apps/(\d+)/")


async def get_steamspy_tag_ids(session: aiohttp.ClientSession) -> dict[str, int]:
    """Récupère tous les tags Steam et leurs IDs depuis SteamSpy.

    Interroge ``GET https://steamspy.com/api.php?request=tags`` et retourne un
    dictionnaire associant le nom du tag (str) à son ID (int). Exemple :
    ``{"Indie": 492, "Roguelite": 1716, ...}``.

    SteamSpy renvoie soit ``{nom: {"id": int, ...}}``, soit ``{nom: count}``
    (valeur numérique sans dict imbriqué) : les deux formats sont gérés. Toute
    erreur (réseau, JSON, format inattendu) retourne un dict vide — ne lève
    jamais.
    """
    params = {"request": "tags"}
    try:
        async with session.get(STEAMSPY_API_URL, params=params) as response:
            response.raise_for_status()
            # content_type=None : SteamSpy renvoie un Content-Type non standard.
            data = await response.json(content_type=None)
    except Exception:
        logger.error("Échec de la récupération des tags SteamSpy", exc_info=True)
        return {}

    if not isinstance(data, dict):
        return {}

    tag_ids: dict[str, int] = {}
    for name, value in data.items():
        if isinstance(value, dict):
            tag_id = value.get("id")
        elif isinstance(value, int):
            tag_id = value
        else:
            tag_id = None
        if isinstance(tag_id, int):
            tag_ids[name] = tag_id
    return tag_ids


async def get_naga_profile(notion_client, database_id: str) -> dict:
    """Lit toutes les pages de la base Notion et retourne les top 5 genres/tags.

    Compte la fréquence des valeurs multi-select « Genres » et « Tags » sur
    l'ensemble des fiches (pagination incluse). Retourne
    ``{"genres": [...], "tags": [...]}`` (listes vides en cas d'erreur).
    """
    genre_counter: Counter = Counter()
    tag_counter: Counter = Counter()
    try:
        cursor = None
        while True:
            kwargs = {"database_id": str(database_id), "page_size": 100}
            if cursor:
                kwargs["start_cursor"] = cursor
            response = await notion_client.databases.query(**kwargs)

            for page in response.get("results", []):
                props = page.get("properties", {})
                for entry in props.get("Genres", {}).get("multi_select", []):
                    name = entry.get("name")
                    if name:
                        genre_counter[name] += 1
                for entry in props.get("Tags", {}).get("multi_select", []):
                    name = entry.get("name")
                    if name:
                        tag_counter[name] += 1

            if response.get("has_more"):
                cursor = response.get("next_cursor")
            else:
                break
    except Exception:
        logger.error("Échec de la lecture du profil NAGA depuis Notion", exc_info=True)
        return {"genres": [], "tags": []}

    return {
        "genres": [name for name, _ in genre_counter.most_common(5)],
        "tags": [name for name, _ in tag_counter.most_common(5)],
    }


async def _fetch_basic_info(
    app_id: int, session: aiohttp.ClientSession
) -> dict | None:
    """Récupère les infos de base d'un jeu via l'API appdetails (ou None)."""
    params = {"appids": str(app_id), "cc": "fr", "l": "english"}
    try:
        async with session.get(STEAM_API_URL, params=params) as response:
            response.raise_for_status()
            payload = await response.json(content_type=None)
    except Exception:
        return None

    entry = payload.get(str(app_id)) if isinstance(payload, dict) else None
    if not entry or not entry.get("success") or "data" not in entry:
        return None

    data = entry["data"]
    return {
        "app_id": app_id,
        "name": data.get("name", ""),
        "steam_url": STEAM_STORE_URL.format(app_id=app_id),
        "genres": [g["description"] for g in data.get("genres", [])],
        "release_date": (data.get("release_date") or {}).get("date", ""),
        "short_description": data.get("short_description", ""),
    }


async def search_steam_suggestions(
    genres: list[str],
    tags: list[str],
    known_app_ids: set[int],
    session: aiohttp.ClientSession,
    max_results: int = 5,
) -> list[dict]:
    """Cherche des jeux indés à venir correspondant au profil NAGA.

    Utilise la recherche du Steam Store (jeux indés, à paraître), filtrée par
    les tags/genres du profil NAGA (logique OU). Pour chaque résultat : extrait
    l'App ID, saute ceux déjà connus, puis récupère les infos de base via
    appdetails. Limité à ``max_results``. Retourne une liste vide sur n'importe
    quelle erreur — ne lève jamais.
    """
    # Résout les noms de tags/genres du profil en IDs Steam via SteamSpy.
    tag_id_map = await get_steamspy_tag_ids(session)
    tag_ids: list[int] = []
    seen_ids: set[int] = set()
    for name in [*tags, *genres]:
        tag_id = tag_id_map.get(name)
        if tag_id is not None and tag_id not in seen_ids:
            seen_ids.add(tag_id)
            tag_ids.append(tag_id)
    tag_ids = tag_ids[:5]

    base_params = {
        "category1": "998",  # jeux uniquement
        "genre": "Indie",
        "filter": "comingsoon",
        "json": "1",
        "cc": "fr",
        "l": "english",
    }
    # Filtre par tags (logique OU) si des IDs ont pu être résolus ; sinon on
    # retombe sur le seul genre=Indie (comportement par défaut).
    if tag_ids:
        base_params["tags"] = ",".join(str(i) for i in tag_ids[:5])

    try:
        # 1. Collecte des candidats sur 3 pages (Steam pagine par 25).
        candidate_ids: list[int] = []
        seen: set[int] = set()
        for start in (0, 25, 50):
            params = {**base_params, "start": str(start)}
            try:
                async with session.get(_SEARCH_URL, params=params) as response:
                    response.raise_for_status()
                    data = await response.json(content_type=None)
            except Exception:
                logger.error(
                    "Échec de la recherche Steam (start=%s)", start, exc_info=True
                )
                continue

            if isinstance(data, dict):
                items = data.get("items", [])
            elif isinstance(data, list):
                items = data
            else:
                items = []

            for item in items:
                logo = (item.get("logo") or "") if isinstance(item, dict) else ""
                match = _LOGO_APPID_RE.search(logo)
                if not match:
                    continue
                app_id = int(match.group(1))
                if app_id in known_app_ids or app_id in seen:
                    continue
                seen.add(app_id)
                candidate_ids.append(app_id)

        # 2. Mélange aléatoire pour varier les suggestions d'un appel à l'autre.
        random.shuffle(candidate_ids)

        # 3. Enrichit (appdetails) jusqu'à max_results candidats valides.
        suggestions: list[dict] = []
        for app_id in candidate_ids:
            if len(suggestions) >= max_results:
                break
            info = await _fetch_basic_info(app_id, session)
            if info is None:
                continue
            suggestions.append(info)
    except Exception:
        logger.error("Échec du traitement des résultats Steam", exc_info=True)
        return []

    return suggestions
