"""Migration ponctuelle : retire les marqueurs ``⟦msg:ID⟧`` du corps des pages
Notion et reporte les id dans la propriété « Message IDs ».

L'idempotence du bot ne repose plus sur un marqueur visible dans le corps de la
page mais sur la propriété rich_text « Message IDs ». Ce script :

1. crée la propriété « Message IDs » si la base ne l'a pas ;
2. pour chaque page, retire le segment marqueur de chaque bloc message (le bloc
   ne garde que « DD.MM.YYYY HH:MM AUTEUR : texte ») et collecte les id ;
3. écrit l'ensemble des id collectés dans la propriété « Message IDs » de la page.

Ré-exécutable sans risque (un corps déjà nettoyé ne contient plus de marqueur) et
doté d'un mode --dry-run.

Usage (depuis la racine du projet) :
    python tools/migrate_notion_format.py            # applique la migration
    python tools/migrate_notion_format.py --dry-run  # affiche sans rien modifier
"""

import sys
from pathlib import Path

# Le bot vit dans bots/ ; on l'ajoute au sys.path pour pouvoir l'importer.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bots"))

import discord_bot as bot  # noqa: E402  (import après ajustement du sys.path)


def block_plain_text(block):
    """Concatène le plain_text des rich_text d'un bloc (paragraphe / puce)."""
    rich = block.get(block.get("type", ""), {}).get("rich_text", [])
    return "".join(part.get("plain_text", "") for part in rich)


def iter_children(notion, page_id):
    """Itère tous les blocs enfants d'une page (avec pagination)."""
    cursor = None
    while True:
        path = f"/blocks/{page_id}/children?page_size=100"
        if cursor:
            path += f"&start_cursor={cursor}"
        result = notion._request("GET", path)
        yield from result.get("results", [])
        if not result.get("has_more"):
            return
        cursor = result.get("next_cursor")


def iter_pages(notion):
    """Itère les (id, titre) de toutes les pages de la base (avec pagination)."""
    cursor = None
    while True:
        payload = {"page_size": 100}
        if cursor:
            payload["start_cursor"] = cursor
        result = notion._request(
            "POST", f"/databases/{notion.database_id}/query", payload
        )
        for page in result.get("results", []):
            title_prop = page.get("properties", {}).get(notion._title_prop, {})
            title = "".join(
                t.get("plain_text", "") for t in title_prop.get("title", [])
            )
            yield page["id"], title
        if not result.get("has_more"):
            return
        cursor = result.get("next_cursor")


def rebuild_segment(content, source):
    """Reconstruit un segment rich_text en préservant annotations et lien."""
    segment = {
        "type": "text",
        "text": {"content": content},
        "annotations": source.get("annotations", {}),
    }
    href = source.get("href")
    if href:
        segment["text"]["link"] = {"url": href}
    return segment


def strip_marker(rich_text):
    """Retire le marqueur d'un bloc. Retourne (message_id|None, nouveaux segments)."""
    message_id = None
    cleaned = []
    for segment in rich_text:
        text = segment.get("plain_text", segment.get("text", {}).get("content", ""))
        if message_id is None:
            match = bot.MSG_MARKER_RE.search(text)
            if match:
                message_id = match.group(1)
                remainder = (text[:match.start()] + text[match.end():]).lstrip()
                if remainder:
                    cleaned.append(rebuild_segment(remainder, segment))
                continue
        cleaned.append(rebuild_segment(
            segment.get("text", {}).get("content", text), segment))
    return message_id, cleaned


def migrate_page(notion, page_id, dry_run):
    """Nettoie les marqueurs d'une page et reporte les id. Retourne le nombre traité."""
    ids = set()
    for block in iter_children(notion, page_id):
        if block.get("type") != "paragraph":
            continue
        message_id, cleaned = strip_marker(block["paragraph"]["rich_text"])
        if message_id is None:
            continue
        ids.add(message_id)
        if not dry_run:
            notion._request(
                "PATCH",
                f"/blocks/{block['id']}",
                {"paragraph": {"rich_text": cleaned}},
            )

    if ids and not dry_run:
        merged = notion.get_recorded_ids(page_id) | ids
        notion.set_recorded_ids(page_id, merged)
    return len(ids)


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    dry_run = "--dry-run" in sys.argv

    env = bot.load_env_file(bot.ENV_NOTION, ["NOTION_TOKEN", "NOTION_PARENT_PAGE_ID"])
    notion = bot.NotionClient(env["NOTION_TOKEN"], env["NOTION_PARENT_PAGE_ID"])
    notion.find_or_create_database()
    if not dry_run:
        notion.ensure_message_ids_property()

    mode = "DRY-RUN (aucune modification)" if dry_run else "MIGRATION"
    print(f"=== {mode} ===")

    total = 0
    for page_id, title in iter_pages(notion):
        count = migrate_page(notion, page_id, dry_run)
        if count:
            print(f"- « {title} » : {count} marqueur(s) nettoyé(s) / id reportés")
        total += count

    verb = "à nettoyer" if dry_run else "nettoyé(s)"
    print(f"Total : {total} marqueur(s) {verb}.")


if __name__ == "__main__":
    main()
