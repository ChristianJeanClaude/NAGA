"""Investigation ponctuelle : pourquoi 16 jeux ne sont pas dans la base Leads.

Croise les deux bases par Thread ID (la vraie clé d'idempotence) et non par nom,
pour distinguer « jamais poussé » de « poussé puis renommé ».
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bots"))

import discord_bot as bot  # noqa: E402
from compare_bases import query_all_pages, resolve_principal, LEADS_TITLE_PROP, LEADS_DB_ID_DEFAULT  # noqa: E402


async def query_pairs(client, db_id, title_prop):
    """[(nom, thread_id)] -> dict thread_id -> nom (ignore les pages sans thread_id)."""
    pages = await query_all_pages(client, db_id, title_prop)
    by_thread = {}
    sans_thread = []
    for nom, thread_id in pages:
        if thread_id:
            by_thread[thread_id] = nom
        else:
            sans_thread.append(nom)
    return by_thread, sans_thread


async def query_last_message(client, db_id, title_prop):
    """thread_id -> date 'Dernier message' (ISO court) pour la base principale."""
    dates = {}
    cursor = None
    while True:
        payload = {"page_size": 100}
        if cursor:
            payload["start_cursor"] = cursor
        result = await client._request("POST", f"/databases/{db_id}/query", payload)
        for page in result.get("results", []):
            props = page.get("properties", {})
            thread_rich = props.get(bot.THREAD_ID_PROP, {}).get("rich_text", [])
            thread_id = "".join(t.get("plain_text", "") for t in thread_rich).strip()
            dm = (props.get("Dernier message", {}).get("date") or {}).get("start")
            if thread_id:
                dates[thread_id] = (dm or "")[:16].replace("T", " ")
        if not result.get("has_more"):
            break
        cursor = result.get("next_cursor")
    return dates


async def run():
    notion_env = bot.load_env_file(bot.ENV_NOTION, ["NOTION_TOKEN", "NOTION_PARENT_PAGE_ID"])
    leads_db_id = notion_env.get("NOTION_DB_LEADS_ID") or LEADS_DB_ID_DEFAULT
    client = bot.NotionClient(notion_env["NOTION_TOKEN"], notion_env["NOTION_PARENT_PAGE_ID"])
    await resolve_principal(client)

    jeux_by_thread, jeux_sans = await query_pairs(client, client.database_id, client._title_prop)
    leads_by_thread, leads_sans = await query_pairs(client, leads_db_id, LEADS_TITLE_PROP)
    derniers = await query_last_message(client, client.database_id, client._title_prop)

    leads_threads = set(leads_by_thread)
    leads_noms = {n.casefold() for n in leads_by_thread.values()}

    # Date de création encodée dans le snowflake Discord (epoch 2015-01-01).
    from datetime import datetime, timezone
    def created(thread_id):
        ms = (int(thread_id) >> 22) + 1420070400000
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")

    print("== 40 threads triés par DERNIER message (✓ = présent dans Leads) ==")
    print("   dernier_msg       créé              statut  nom")
    for thread_id, nom in sorted(jeux_by_thread.items(), key=lambda kv: derniers.get(kv[0], "")):
        flag = "✓" if thread_id in leads_threads else "✗"
        print(f"  {derniers.get(thread_id,'?'):16}  {created(thread_id)}  {flag}    {nom}")
    print()

    # Pour chaque page principale, classer le statut côté Leads.
    jamais_pousse = []   # thread_id absent ET nom absent → vraiment jamais poussé
    renomme = []         # thread_id présent mais nom différent → poussé puis renommé
    nom_seul = []        # nom présent mais thread_id différent → doublon/thread distinct

    for thread_id, nom in jeux_by_thread.items():
        if thread_id in leads_threads:
            if leads_by_thread[thread_id].casefold() != nom.casefold():
                renomme.append((nom, thread_id, leads_by_thread[thread_id]))
        elif nom.casefold() in leads_noms:
            nom_seul.append((nom, thread_id))
        else:
            jamais_pousse.append((nom, thread_id))

    print(f"Base principale : {len(jeux_by_thread)} pages avec thread_id, {len(jeux_sans)} sans")
    print(f"Base Leads      : {len(leads_by_thread)} pages avec thread_id, {len(leads_sans)} sans")
    print(f"\n== Jamais poussés (thread_id ET nom absents des Leads) : {len(jamais_pousse)} ==")
    for nom, thread_id in sorted(jamais_pousse, key=lambda x: x[0].casefold()):
        print(f"  - {nom}  (thread {thread_id})")
    print(f"\n== Poussés puis renommés (thread_id présent, nom différent) : {len(renomme)} ==")
    for nom, thread_id, ancien in renomme:
        print(f"  - principale « {nom} » vs leads « {ancien} »  (thread {thread_id})")
    print(f"\n== Nom présent mais via un autre thread : {len(nom_seul)} ==")
    for nom, thread_id in nom_seul:
        print(f"  - {nom}  (thread {thread_id})")
    if leads_sans:
        print(f"\n== Leads sans thread_id : {len(leads_sans)} ==")
        for nom in leads_sans:
            print(f"  - {nom}")

    # --- Corrélation : présence de liens url-typés (YouTube/Twitter/Pitch/Exec) ---
    async def page_ids_by_thread(db_id, title_prop):
        out = {}
        cursor = None
        while True:
            payload = {"page_size": 100}
            if cursor:
                payload["start_cursor"] = cursor
            res = await client._request("POST", f"/databases/{db_id}/query", payload)
            for page in res.get("results", []):
                tr = page.get("properties", {}).get(bot.THREAD_ID_PROP, {}).get("rich_text", [])
                tid = "".join(t.get("plain_text", "") for t in tr).strip()
                if tid:
                    out[tid] = page["id"]
            if not res.get("has_more"):
                break
            cursor = res.get("next_cursor")
        return out

    async def page_links(page_id):
        textes = []
        cursor = None
        while True:
            path = f"/blocks/{page_id}/children?page_size=100"
            if cursor:
                path += f"&start_cursor={cursor}"
            res = await client._request("GET", path)
            for block in res.get("results", []):
                btype = block.get("type")
                for rt in block.get(btype, {}).get("rich_text", []) if isinstance(block.get(btype), dict) else []:
                    textes.append(rt.get("plain_text", ""))
                    href = rt.get("href")
                    if href:
                        textes.append(href)
            if not res.get("has_more"):
                break
            cursor = res.get("next_cursor")
        return bot.extract_links("\n".join(textes))

    page_ids = await page_ids_by_thread(client.database_id, client._title_prop)
    print("\n== Corrélation : liens url-typés (YouTube/Twitter/Pitch/Exec) par groupe ==")
    for label, group in (("ABSENTS des Leads", jamais_pousse), ("PRÉSENTS dans Leads",
                          [(n, t) for t, n in jeux_by_thread.items() if t in leads_threads])):
        avec = 0
        details = []
        for nom, thread_id in group:
            pid = page_ids.get(thread_id)
            if not pid:
                continue
            liens = await page_links(pid)
            _, _, pitch, execs, yts, tws, _, _, _ = bot._sort_liens(liens, "\n".join(liens))
            cats = []
            if yts: cats.append("YouTube")
            if tws: cats.append("Twitter")
            if pitch: cats.append("Pitch")
            if execs: cats.append("Exec")
            if cats:
                avec += 1
                details.append(f"      {nom} → {', '.join(cats)}")
        print(f"  {label} : {avec}/{len(group)} contiennent un lien url-typé")
        for d in details:
            print(d)


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    asyncio.run(run())


if __name__ == "__main__":
    main()
