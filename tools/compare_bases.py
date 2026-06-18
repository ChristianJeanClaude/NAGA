"""Diagnostic : jeux présents dans « NAGA — Jeux Discord » mais absents de « Leads Discord ».

Compare les titres des pages des deux bases Notion et liste ceux qui existent
dans la base principale (une page par thread de forum) sans contrepartie dans la
base Leads. La comparaison est insensible à la casse et aux espaces de bordure.

Lecture seule : la base principale est résolue sans jamais être créée.

Usage (depuis la racine) :
    python tools/compare_bases.py
    python tools/compare_bases.py --doublons   # liste aussi les doublons côté principale
"""

import argparse
import asyncio
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bots"))

import discord_bot as bot  # noqa: E402

LEADS_TITLE_PROP = "Nom du jeu"
LEADS_DB_ID_DEFAULT = "37277389-df89-809e-a356-c242c5e43cbb"


async def resolve_principal(client):
    """Mémorise l'id et la propriété titre de la base principale, sans la créer."""
    try:
        existing = await client._request("GET", f"/databases/{client._config_id}")
    except bot.NotionError:
        existing = None
    if existing is not None:
        client._adopt_database(existing)
        return
    result = await client._request(
        "POST", "/search",
        {"query": bot.DB_NAME, "filter": {"value": "database", "property": "object"}},
    )
    for item in result.get("results", []):
        if client._database_title(item) == bot.DB_NAME:
            client._adopt_database(item)
            return
    raise bot.NotionError(f"Base « {bot.DB_NAME} » introuvable (non créée : lecture seule).")


async def query_all_pages(client, db_id, title_prop):
    """Retourne [(titre, thread_id)] pour toutes les pages non vides d'une base (paginé).

    ``thread_id`` vaut None si la base n'expose pas la propriété « Thread ID ».
    """
    pages = []
    cursor = None
    while True:
        payload = {"page_size": 100}
        if cursor:
            payload["start_cursor"] = cursor
        result = await client._request("POST", f"/databases/{db_id}/query", payload)
        for page in result.get("results", []):
            props = page.get("properties", {})
            rich = props.get(title_prop, {}).get("title", [])
            name = "".join(t.get("plain_text", "") for t in rich).strip()
            if not name:
                continue
            thread_rich = props.get(bot.THREAD_ID_PROP, {}).get("rich_text", [])
            thread_id = "".join(t.get("plain_text", "") for t in thread_rich).strip() or None
            pages.append((name, thread_id))
        if not result.get("has_more"):
            break
        cursor = result.get("next_cursor")
    return pages


def find_duplicates(pages):
    """Regroupe les pages dont le titre partage la même forme normalisée (casse/espaces).

    ``pages`` est une liste de (titre, thread_id). Retourne une liste de
    (titre_représentatif, [(titre, thread_id), ...]) pour les groupes apparaissant
    plus d'une fois, triée par nombre d'occurrences décroissant.
    """
    groupes = defaultdict(list)
    for nom, thread_id in pages:
        groupes[nom.casefold()].append((nom, thread_id))
    doublons = [(membres[0][0], membres) for membres in groupes.values() if len(membres) > 1]
    doublons.sort(key=lambda item: (-len(item[1]), item[0].casefold()))
    return doublons


async def run(show_duplicates):
    notion_env = bot.load_env_file(bot.ENV_NOTION, ["NOTION_TOKEN", "NOTION_PARENT_PAGE_ID"])
    leads_db_id = notion_env.get("NOTION_DB_LEADS_ID") or LEADS_DB_ID_DEFAULT

    client = bot.NotionClient(notion_env["NOTION_TOKEN"], notion_env["NOTION_PARENT_PAGE_ID"])
    await resolve_principal(client)

    jeux = await query_all_pages(client, client.database_id, client._title_prop)
    leads = await query_all_pages(client, leads_db_id, LEADS_TITLE_PROP)

    leads_index = {nom.casefold() for nom, _ in leads}
    manquants = [nom for nom, _ in jeux if nom.casefold() not in leads_index]

    print(f"Base principale « {bot.DB_NAME} » : {len(jeux)} jeu(x)")
    print(f"Base Leads {leads_db_id} : {len(leads)} jeu(x)")
    print(f"\nPrésents dans la base principale mais absents des Leads : {len(manquants)}")
    for nom in sorted(manquants, key=str.casefold):
        print(f"  - {nom}")

    if show_duplicates:
        doublons = find_duplicates(jeux)
        print(f"\nDoublons côté base principale : {len(doublons)} nom(s) sur plusieurs pages")
        for representatif, membres in doublons:
            print(f"  - {representatif} ×{len(membres)}")
            for nom, thread_id in membres:
                detail = f" — « {nom} »" if nom != representatif else ""
                print(f"      Thread ID {thread_id or '(absent)'}{detail}")


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Compare les bases NAGA et Leads Discord.")
    parser.add_argument(
        "--doublons", action="store_true",
        help="liste aussi les noms présents sur plusieurs pages de la base principale",
    )
    args = parser.parse_args()
    asyncio.run(run(args.doublons))


if __name__ == "__main__":
    main()
