"""Diagnostic : liste toutes les bases Notion accessibles avec le NOTION_TOKEN.

Interroge /v1/search (filter type=database) et affiche pour chaque base : nom, id,
nombre de propriétés, et parent (type + id) afin d'identifier les relations
base mère / sous-bases. Lecture seule.

Usage (depuis la racine) :
    python tools/list_databases.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bots"))

import discord_bot as bot  # noqa: E402


def _title(db):
    return "".join(t.get("plain_text", "") for t in db.get("title", [])) or "(sans titre)"


def _parent(db):
    parent = db.get("parent", {})
    ptype = parent.get("type", "?")
    pid = parent.get(ptype) if ptype != "workspace" else "workspace"
    return ptype, pid


async def run():
    notion_env = bot.load_env_file(bot.ENV_NOTION, ["NOTION_TOKEN", "NOTION_PARENT_PAGE_ID"])
    client = bot.NotionClient(notion_env["NOTION_TOKEN"], notion_env["NOTION_PARENT_PAGE_ID"])

    bases = []
    cursor = None
    while True:
        payload = {"filter": {"value": "database", "property": "object"}, "page_size": 100}
        if cursor:
            payload["start_cursor"] = cursor
        result = await client._request("POST", "/search", payload)
        bases.extend(result.get("results", []))
        if not result.get("has_more"):
            break
        cursor = result.get("next_cursor")

    print(f"{len(bases)} base(s) accessible(s) avec ce token :\n")
    for db in sorted(bases, key=lambda d: _title(d).casefold()):
        ptype, pid = _parent(db)
        nb = len(db.get("properties", {}))
        print(f"  {_title(db)}")
        print(f"      id     : {db['id']}")
        print(f"      props  : {nb}")
        print(f"      parent : {ptype} -> {pid}")
        print()


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    asyncio.run(run())


if __name__ == "__main__":
    main()
