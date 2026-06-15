"""Module de Djoundounda : upsert de leads dans une base Notion dédiée.

Copié depuis ~/Downloads/notion_leads.py. Seule adaptation : le token est lu
depuis NOTION_TOKEN_LEADS (au lieu de NOTION_TOKEN) afin de cohabiter avec le
client Notion principal du bot ; ``.get(..., "")`` garde l'import sûr si la clé
est absente (l'échec surviendra alors lors de l'appel HTTP, pas à l'import).
"""

import json
import os
import urllib.error
import urllib.request

NOTION_TOKEN = os.environ.get("NOTION_TOKEN_LEADS", "")
DB_ID = "37277389-df89-809e-a356-c242c5e43cbb"
NOTION_VERSION = "2022-06-28"
BASE_URL = "https://api.notion.com/v1"


def _request(method: str, path: str, body: dict | None = None) -> dict:
    url = f"{BASE_URL}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {NOTION_TOKEN}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            notion_error = json.loads(body)
            print(f"Erreur Notion {e.code} : {notion_error.get('code')} — {notion_error.get('message')}")
        except json.JSONDecodeError:
            print(f"Erreur HTTP {e.code} : {body}")
        raise


# Propriétés toujours écrasées lors d'une mise à jour, même si elles ont déjà une valeur.
ALWAYS_UPDATE = {"Messages", "Dernier message", "Tags"}


def _is_empty(prop: dict) -> bool:
    """Retourne True si une propriété Notion ne contient aucune valeur."""
    ptype = prop.get("type")
    if ptype in ("rich_text", "title", "files"):
        return not prop.get(ptype)
    if ptype in ("date", "url", "number", "select"):
        return prop.get(ptype) is None
    if ptype == "multi_select":
        return not prop.get("multi_select")
    return False


def _build_properties(data: dict) -> dict:
    props: dict = {
        "Nom du jeu": {"title": [{"text": {"content": data["nom_du_jeu"]}}]},
    }
    if data.get("source"):
        props["Source"] = {"rich_text": [{"text": {"content": data["source"]}}]}
    if data.get("date"):
        props["Dernier message"] = {"date": {"start": data["date"]}}
    if data.get("messages"):
        props["Messages"] = {"rich_text": [{"text": {"content": data["messages"]}}]}
    if data.get("liens"):
        props["Liens"] = {"rich_text": [{"text": {"content": data["liens"]}}]}
    if data.get("pieces_jointes"):
        props["Pièces jointes"] = {
            "files": [
                {"name": url.split("/")[-1][:100], "external": {"url": url}}
                for url in data["pieces_jointes"]
            ]
        }
    if data.get("thread_id"):
        props["Thread ID"] = {"rich_text": [{"text": {"content": str(data["thread_id"])}}]}
    if data.get("steam_url"):
        props["Steam URL"] = {"url": data["steam_url"]}
    if data.get("kickstarter"):
        props["Kickstarter"] = {"url": data["kickstarter"]}
    if data.get("pitch_decks"):
        props["Pitch Deck"] = {"rich_text": [{"text": {"content": "\n".join(data["pitch_decks"])[:2000]}}]}
    if data.get("exec_docs"):
        props["Exec Doc"] = {"rich_text": [{"text": {"content": "\n".join(data["exec_docs"])[:2000]}}]}
    if data.get("youtubes"):
        props["YouTube"] = {"rich_text": [{"text": {"content": "\n".join(data["youtubes"])[:2000]}}]}
    if data.get("twitters"):
        props["Twitter"] = {"rich_text": [{"text": {"content": "\n".join(data["twitters"])[:2000]}}]}
    if data.get("autres_steam_urls"):
        props["Autres Steam URLs"] = {"rich_text": [{"text": {"content": data["autres_steam_urls"]}}]}
    if data.get("studio"):
        props["Studio"] = {"rich_text": [{"text": {"content": data["studio"]}}]}
    if data.get("website_studio"):
        props["Website studio"] = {"url": data["website_studio"]}
    if data.get("description_jeu"):
        props["Description jeux"] = {"rich_text": [{"text": {"content": data["description_jeu"]}}]}
    if data.get("email"):
        props["Email"] = {"email": data["email"]}
    if data.get("tags"):
        props["Tags"] = {"multi_select": [{"name": t} for t in data["tags"]]}
    if data.get("summary"):
        props["Summary"] = {"rich_text": [{"text": {"content": data["summary"][:2000]}}]}
    if data.get("dernier_resume"):
        props["Dernier résumé"] = {"date": {"start": data["dernier_resume"]}}
    return props


def push_to_notion(data: dict) -> dict:
    """Upsert un lead dans la base Notion. Crée la page si le jeu n'existe pas, la met à jour sinon.

    Format JSON attendu :
    {
        "nom_du_jeu":     str,           # requis — clé de déduplication de repli
        "thread_id":      str | int,     # optionnel — clé de déduplication principale
        "source":         str,           # ex. "Discord #game-releases"
        "date":           str,           # ISO 8601, ex. "2026-06-01"
        "messages":       str,           # contenu du message Discord
        "liens":          str,           # URL associée
        "pieces_jointes": list[str],     # liste d'URLs (CDN Discord, etc.)
        "steam_url":      str,           # fiche Steam du jeu
        "kickstarter":    str,           # campagne Kickstarter
        "pitch_deck":     str,           # lien vers le pitch deck
        "studio":         str,           # nom du studio
        "email":          str,           # contact du studio
    }

    Retourne {"action": "created"|"updated", "id": "<page_id>"}.
    """
    if data.get("thread_id"):
        result = _request("POST", f"/databases/{DB_ID}/query", {
            "filter": {
                "property": "Thread ID",
                "rich_text": {"equals": str(data["thread_id"])},
            }
        })
    else:
        result = _request("POST", f"/databases/{DB_ID}/query", {
            "filter": {
                "property": "Nom du jeu",
                "title": {"equals": data["nom_du_jeu"]},
            }
        })

    properties = _build_properties(data)

    if result["results"]:
        page_id = result["results"][0]["id"]
        existing_props = _request("GET", f"/pages/{page_id}").get("properties", {})
        patch = {
            name: value
            for name, value in properties.items()
            if name in ALWAYS_UPDATE or _is_empty(existing_props.get(name, {}))
        }
        if patch:
            _request("PATCH", f"/pages/{page_id}", {"properties": patch})
        return {"action": "updated", "id": page_id}
    else:
        page = _request("POST", "/pages", {
            "parent": {"database_id": DB_ID},
            "properties": properties,
        })
        return {"action": "created", "id": page["id"]}


def ensure_schema() -> None:
    """Crée Summary et Dernier résumé dans la base Leads si elles sont absentes."""
    db = _request("GET", f"/databases/{DB_ID}")
    existing = db.get("properties", {})
    patch = {}
    if "Summary" not in existing:
        patch["Summary"] = {"rich_text": {}}
    if "Dernier résumé" not in existing:
        patch["Dernier résumé"] = {"date": {}}
    if "Pitch Deck" not in existing:
        patch["Pitch Deck"] = {"rich_text": {}}
    if "Exec Doc" not in existing:
        patch["Exec Doc"] = {"rich_text": {}}
    if "YouTube" not in existing:
        patch["YouTube"] = {"rich_text": {}}
    if "Twitter" not in existing:
        patch["Twitter"] = {"rich_text": {}}
    if patch:
        _request("PATCH", f"/databases/{DB_ID}", {"properties": patch})


def get_page_summary_info(page_id: str) -> dict:
    """Retourne la date Dernier résumé et le texte Messages d'une page lead."""
    page = _request("GET", f"/pages/{page_id}")
    props = page.get("properties", {})

    date_prop = props.get("Dernier résumé", {})
    dernier_resume = (date_prop.get("date") or {}).get("start")

    messages_prop = props.get("Messages", {})
    messages = "".join(t.get("plain_text", "") for t in messages_prop.get("rich_text", []))

    return {"page_id": page_id, "dernier_resume": dernier_resume, "messages": messages}


def update_summary(page_id: str, summary: str, date_str: str) -> None:
    """Met à jour Summary et Dernier résumé sur une page lead."""
    _request("PATCH", f"/pages/{page_id}", {"properties": {
        "Summary": {"rich_text": [{"text": {"content": summary[:2000]}}]},
        "Dernier résumé": {"date": {"start": date_str}},
    }})


def trigger_ai_summary(page_id: str, messages_text: str, date_str: str) -> None:
    """Re-écrit Messages pour déclencher le remplissage auto Notion IA sur Summary.

    Notion IA recalcule Summary automatiquement quand sa source (Messages) change.
    Stamp Dernier résumé dans le même appel pour tracker la dernière régénération.
    """
    _request("PATCH", f"/pages/{page_id}", {"properties": {
        "Messages": {"rich_text": [{"text": {"content": messages_text[:2000]}}]},
        "Dernier résumé": {"date": {"start": date_str}},
    }})


if __name__ == "__main__":
    TEST_DATA = {
        "thread_id": "1234567890123456789",
        "nom_du_jeu": "Hollow Knight: Silksong",
        "source": "Discord #game-releases",
        "date": "2026-06-01",
        "messages": "La date de sortie vient d'être confirmée ! Disponible le 15 juin sur toutes plateformes.",
        "liens": "https://store.steampowered.com/app/1030300",
        "pieces_jointes": [
            "https://cdn.discordapp.com/attachments/1234567890/screenshot_silksong.png"
        ],
        "statut": "Nouveau",
    }
    result = push_to_notion(TEST_DATA)
    print(f"[{result['action'].upper()}] {result['id']}")
