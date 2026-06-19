"""Réconciliation CRM : associe les threads Discord aux lignes manuelles du CRM.

Beaucoup de lignes du CRM « Pipeline Prospects Jeux » ont été saisies à la main,
sans Thread ID. Sans réconciliation, le bot (qui déduplique par Thread ID) créerait
des doublons. Ce script associe chaque thread du forum à une ligne CRM existante
par nom normalisé (casse/accents/ponctuation ignorés), puis :

- dry-run (défaut) : affiche la proposition (associés / nouveaux / non associés) ;
- --apply : écrit le Thread ID dans les lignes CRM associées (adoption), sans
  toucher aux autres champs. Le bot pourra alors les mettre à jour sans doublon.

Connexion Discord en LECTURE SEULE (assure-toi qu'aucune autre instance du bot
ne tourne avec le même token, sinon conflit de session).

Usage (depuis la racine) :
    python tools/reconcile_crm.py            # proposition seule
    python tools/reconcile_crm.py --apply    # écrit les Thread ID dans le CRM
"""

import asyncio
import os
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bots"))

import discord  # noqa: E402
import discord_bot as bot  # noqa: E402


def norm(s: str) -> str:
    """Normalise un nom pour comparaison : minuscules, sans accents ni ponctuation."""
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9 ]", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


# Correspondances validées à la main (noms normalisés) que le match auto rate.
# Clé = nom de thread normalisé, valeur = nom de ligne CRM normalisé.
MANUAL_MATCH = {
    "adventurer s guild": "adventurer s guild inc",
    "fragmented of echoes": "fragmented echoes veil of memories",
    "redlock studio hypnos": "hypnos",
    "moon moon the sleepy and the big step": "the sleepy the big step betaloom",
    "papernight": "paperknight",
}


class ThreadLister(discord.Client):
    def __init__(self, channel_id, **kwargs):
        super().__init__(**kwargs)
        self._channel_id = channel_id
        self.threads = []  # [(thread_id, name)]

    async def on_ready(self):
        channel = self.get_channel(self._channel_id) or await self.fetch_channel(self._channel_id)
        if isinstance(channel, discord.ForumChannel):
            for thread in await bot.all_forum_threads(channel):
                self.threads.append((str(thread.id), thread.name))
        await self.close()


def crm_rows(nl, title_prop):
    """[(nom, page_id, thread_id)] de toutes les pages du CRM."""
    rows, cursor = [], None
    while True:
        payload = {"page_size": 100}
        if cursor:
            payload["start_cursor"] = cursor
        r = nl._request("POST", f"/databases/{nl.DB_ID}/query", payload)
        for pg in r["results"]:
            p = pg["properties"]
            nom = "".join(t.get("plain_text", "") for t in p.get(title_prop, {}).get("title", []))
            tid = "".join(t.get("plain_text", "") for t in p.get("Thread ID", {}).get("rich_text", []))
            rows.append((nom.strip(), pg["id"], tid.strip()))
        if not r.get("has_more"):
            break
        cursor = r["next_cursor"]
    return rows


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    apply = "--apply" in sys.argv

    # 1) Threads Discord (lecture seule).
    discord_env = bot.load_env_file(bot.ENV_DISCORD, ["DISCORD_TOKEN", "DISCORD_CHANNEL_ID"])
    intents = discord.Intents.default()
    intents.message_content = True
    lister = ThreadLister(int(discord_env["DISCORD_CHANNEL_ID"]), intents=intents)
    lister.run(discord_env["DISCORD_TOKEN"], log_handler=None)
    threads = lister.threads
    print(f"{len(threads)} thread(s) Discord récupéré(s).")

    # 2) Lignes du CRM.
    notion_env = bot.load_env_file(bot.ENV_NOTION, ["NOTION_TOKEN"])
    os.environ["NOTION_TOKEN_LEADS"] = notion_env.get("NOTION_TOKEN_LEADS") or notion_env["NOTION_TOKEN"]
    if notion_env.get("NOTION_DB_LEADS_ID"):
        os.environ["NOTION_DB_LEADS_ID"] = notion_env["NOTION_DB_LEADS_ID"]
    import notion_leads as nl
    db = nl._request("GET", f"/databases/{nl.DB_ID}")
    title_prop = next((n for n, s in db["properties"].items() if s["type"] == "title"), "Jeu")
    rows = crm_rows(nl, title_prop)
    print(f"{len(rows)} ligne(s) CRM. Propriété titre : « {title_prop} ».\n")

    # Index CRM par nom normalisé.
    crm_index = {}
    for nom, page_id, tid in rows:
        crm_index.setdefault(norm(nom), []).append((nom, page_id, tid))

    associes, nouveaux, ambigus = [], [], []
    crm_matched = set()
    for thread_id, name in threads:
        nkey = norm(name)
        cands = crm_index.get(MANUAL_MATCH.get(nkey, nkey), [])
        if len(cands) == 1:
            associes.append((name, thread_id, cands[0]))
            crm_matched.add(cands[0][1])
        elif len(cands) > 1:
            ambigus.append((name, thread_id, cands))
        else:
            nouveaux.append((name, thread_id))

    print(f"== ASSOCIÉS ({len(associes)}) : thread ↔ ligne CRM ==")
    for tname, tid, (cnom, pid, ctid) in sorted(associes, key=lambda x: x[0].casefold()):
        flag = " (Thread ID déjà présent)" if ctid else ""
        print(f"  ✓ « {tname} »  ↔  CRM « {cnom} »  [thread {tid}]{flag}")
    print(f"\n== NOUVEAUX ({len(nouveaux)}) : threads sans ligne CRM → seront créés par le bot ==")
    for tname, tid in sorted(nouveaux, key=lambda x: x[0].casefold()):
        print(f"  + « {tname} »  [thread {tid}]")
    if ambigus:
        print(f"\n== AMBIGUS ({len(ambigus)}) : plusieurs lignes CRM possibles, à traiter à la main ==")
        for tname, tid, cands in ambigus:
            print(f"  ? « {tname} » → {[c[0] for c in cands]}")
    non_assoc = [(nom, pid, tid) for nom, pid, tid in rows if pid not in crm_matched]
    print(f"\n== LIGNES CRM NON ASSOCIÉES ({len(non_assoc)}) : restent telles quelles ==")
    for nom, pid, tid in sorted(non_assoc, key=lambda x: x[0].casefold()):
        print(f"  · « {nom} »")

    if not apply:
        print("\n>>> DRY-RUN : aucun écrit. Relance avec --apply pour écrire les Thread ID.")
        return

    print("\n>>> APPLY : écriture des Thread ID dans les lignes CRM associées…")
    written = 0
    for tname, tid, (cnom, pid, ctid) in associes:
        if ctid == tid:
            continue
        nl._request("PATCH", f"/pages/{pid}", {"properties": {"Thread ID": nl._rich_text(tid)}})
        written += 1
    print(f"{written} ligne(s) CRM adoptée(s) (Thread ID écrit).")


if __name__ == "__main__":
    main()
