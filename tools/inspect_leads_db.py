"""Diagnostic : affiche le schéma de la base Notion Leads (nom → type de propriété).

Reproduit ce que fait le bot au démarrage : lit NOTION_TOKEN_LEADS depuis
~/.env.notion, l'injecte dans l'environnement, puis interroge la base dont l'id
est codé en dur dans notion_leads.DB_ID. Lecture seule, aucun écrit.

Utile pour vérifier que le token Leads est valide et que le schéma attendu par
push_to_notion existe bien côté Notion.

Usage (depuis la racine) :
    python tools/inspect_leads_db.py
"""

import os
import sys
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bots"))

import discord_bot as bot  # noqa: E402


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    # Même source et même fallback que le bot : ~/.env.notion (pas de dotenv),
    # NOTION_TOKEN_LEADS sinon NOTION_TOKEN.
    notion_env = bot.load_env_file(bot.ENV_NOTION, ["NOTION_TOKEN"])
    leads_token = notion_env.get("NOTION_TOKEN_LEADS") or notion_env.get("NOTION_TOKEN")
    if not leads_token:
        print("Aucun token Notion dans ~/.env.notion : base Leads non configurée.")
        return

    # notion_leads lit token et id À l'import : renseigner l'environnement d'abord.
    os.environ["NOTION_TOKEN_LEADS"] = leads_token
    leads_db_id = notion_env.get("NOTION_DB_LEADS_ID")
    if leads_db_id:
        os.environ["NOTION_DB_LEADS_ID"] = leads_db_id
    import notion_leads  # noqa: E402

    try:
        db = notion_leads._request("GET", f"/databases/{notion_leads.DB_ID}")
    except urllib.error.HTTPError:
        # _request a déjà loggué le code et le message Notion ; pas de traceback.
        return
    properties = db.get("properties", {})

    print(f"Base Leads {notion_leads.DB_ID} — {len(properties)} propriété(s) :")
    for name, prop in sorted(properties.items()):
        print(f"  {name} -> {prop['type']}")


if __name__ == "__main__":
    main()
