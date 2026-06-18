"""Diagnostic : pages Leads dont « Website studio » est rempli (nom + URL).

Aide à repérer les liens qui ne sont pas de vrais sites de studio et qui sont
tombés dans « Website studio » par défaut (drive.google.com, docs.google.com,
notion.site, linktr.ee, etc.) afin de leur créer une catégorie dédiée si besoin.
Les URLs jugées suspectes sont préfixées « ⚠ ».

Lecture seule.

Usage (depuis la racine) :
    python tools/list_website_studio.py
"""

import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bots"))

import discord_bot as bot  # noqa: E402
from compare_bases import LEADS_DB_ID_DEFAULT  # noqa: E402

WEBSITE_PROP = "Website studio"
TITLE_PROP = "Nom du jeu"

# Domaines qui ne sont presque jamais le site officiel d'un studio.
SUSPECT_RE = re.compile(
    r"drive\.google\.com|docs\.google\.com|fathom\.video|youtube\.com|youtu\.be"
    r"|x\.com|twitter\.com|kickstarter\.com|notion\.site|linktr\.ee|discord\.(gg|com)"
    r"|store\.steampowered\.com|\.pdf",
    re.IGNORECASE,
)


async def run():
    notion_env = bot.load_env_file(bot.ENV_NOTION, ["NOTION_TOKEN", "NOTION_PARENT_PAGE_ID"])
    leads_db_id = notion_env.get("NOTION_DB_LEADS_ID") or LEADS_DB_ID_DEFAULT
    client = bot.NotionClient(notion_env["NOTION_TOKEN"], notion_env["NOTION_PARENT_PAGE_ID"])

    lignes = []
    cursor = None
    while True:
        payload = {"page_size": 100}
        if cursor:
            payload["start_cursor"] = cursor
        result = await client._request("POST", f"/databases/{leads_db_id}/query", payload)
        for page in result.get("results", []):
            props = page.get("properties", {})
            url = (props.get(WEBSITE_PROP, {}) or {}).get("url")
            if not url:
                continue
            rich = props.get(TITLE_PROP, {}).get("title", [])
            nom = "".join(t.get("plain_text", "") for t in rich).strip() or "(sans nom)"
            lignes.append((nom, url, bool(SUSPECT_RE.search(url))))
        if not result.get("has_more"):
            break
        cursor = result.get("next_cursor")

    suspects = sum(1 for _, _, s in lignes if s)
    print(f"{len(lignes)} page(s) avec « {WEBSITE_PROP} » rempli ({suspects} suspecte(s)) :\n")
    for nom, url, suspect in sorted(lignes, key=lambda x: (not x[2], x[0].casefold())):
        marqueur = "⚠ " if suspect else "  "
        print(f"  {marqueur}{nom}")
        print(f"      {url}")


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    asyncio.run(run())


if __name__ == "__main__":
    main()
