"""Module de Djoundounda : upsert de leads dans une base Notion dédiée.

Copié depuis ~/Downloads/notion_leads.py. Seule adaptation : le token est lu
depuis NOTION_TOKEN_LEADS (au lieu de NOTION_TOKEN) afin de cohabiter avec le
client Notion principal du bot ; ``.get(..., "")`` garde l'import sûr si la clé
est absente (l'échec surviendra alors lors de l'appel HTTP, pas à l'import).
"""

import hashlib
import json
import os
import urllib.error
import urllib.request

NOTION_TOKEN = os.environ.get("NOTION_TOKEN_LEADS", "")
# Id de la base Leads : configurable via NOTION_DB_LEADS_ID (~/.env.notion),
# avec l'ancienne valeur codée en dur comme repli rétrocompatible.
DB_ID = os.environ.get("NOTION_DB_LEADS_ID") or "37277389-df89-809e-a356-c242c5e43cbb"
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


# Propriété titre de la base cible. « Nom du jeu » par défaut (ancienne base de
# test) ; détectée dynamiquement par ensure_schema() pour s'adapter à n'importe
# quelle base — ex. « Jeu » dans le CRM « Pipeline Prospects Jeux ».
_TITLE_PROP = "Nom du jeu"

# Propriétés toujours écrasées lors d'une mise à jour, même si elles ont déjà une valeur.
# (Champs gérés par le bot ; les champs humains du CRM ne sont jamais dans cette liste.)
ALWAYS_UPDATE = {"Messages", "Dernier échange", "Tags"}

# Colonnes gérées par le bot, avec leur type Notion. ensure_schema() les crée si
# absentes (et corrige en rich_text celles qui existeraient avec un autre type).
# Le titre n'y figure pas (une base a un unique titre, détecté à l'exécution).
# Les champs humains du CRM (Statut, Priorité, Tier, Type de deal, Owner NAGA,
# Revshare cherché, WL estimées, Deadline, Next step, Notes, Insights) sont
# volontairement absents : le bot ne les crée ni ne les modifie.
MANAGED_COLUMNS = {
    "Thread ID": {"rich_text": {}},          # clé d'idempotence (technique)
    "Source": {"rich_text": {}},
    "Dernier échange": {"date": {}},         # natif CRM : date du dernier message Discord
    "Messages": {"rich_text": {}},
    "Liens": {"rich_text": {}},
    "Pièces jointes": {"files": {}},
    "Lien Steam": {"url": {}},               # natif CRM
    "Kickstarter": {"url": {}},
    "Pitch Deck": {"rich_text": {}},
    "Exec Doc": {"rich_text": {}},
    "YouTube": {"rich_text": {}},
    "Twitter": {"rich_text": {}},
    "Enregistrement fathom": {"rich_text": {}},
    "Drive / Assets": {"rich_text": {}},
    "Instagram": {"rich_text": {}},
    "Canva": {"rich_text": {}},
    "Autres Steam URLs": {"rich_text": {}},
    "Studio": {"rich_text": {}},             # natif CRM
    "Website studio": {"url": {}},
    "Description jeux": {"rich_text": {}},
    "Email": {"email": {}},
    "Tags": {"multi_select": {}},
    "Summary": {"rich_text": {}},
    "Dernier résumé": {"date": {}},
    "Conv sync": {"rich_text": {}},          # technique : empreinte de la conversation écrite dans le corps
}

# Titre du bloc qui ouvre la section conversation dans le corps de la page.
# Sert de marqueur : tout ce qui le suit appartient au bot et est réécrit ;
# le contenu humain placé AU-DESSUS du marqueur est préservé.
CONV_MARKER = "💬 Conversation Discord (synchronisée automatiquement)"
# Nombre max de blocs par requête d'ajout d'enfants (limite API Notion).
NOTION_CHILDREN_BATCH = 100

# Sous-ensemble rich_text (utilisé pour la correction de type et les tests).
RICH_TEXT_COLUMNS = tuple(name for name, spec in MANAGED_COLUMNS.items() if "rich_text" in spec)

# Limite Notion : un objet text de rich_text refuse plus de 2000 caractères.
# Au-delà, l'API rejette tout le push — on tronque donc systématiquement.
NOTION_RICH_TEXT_LIMIT = 2000


def _truncate_utf16(content: str, limit: int) -> str:
    """Tronque à `limit` unités UTF-16 — l'unité réellement comptée par Notion.

    Un slice Python (``content[:limit]``) compte les points de code : un emoji
    hors BMP (ex. 🧱) vaut 1 point de code mais 2 unités UTF-16, si bien qu'une
    chaîne de 2000 points de code peut atteindre 2002 unités et faire rejeter le
    push (HTTP 400). On tronque donc sur la longueur UTF-16.
    """
    encoded = content.encode("utf-16-le")
    if len(encoded) <= limit * 2:
        return content
    # Couper sur une frontière d'unité ; errors='ignore' jette une demi-paire de substitution.
    return encoded[: limit * 2].decode("utf-16-le", errors="ignore")


def _rich_text(content: str) -> dict:
    """Construit une propriété rich_text en tronquant à la limite Notion (2000 unités UTF-16)."""
    return {"rich_text": [{"text": {"content": _truncate_utf16(content, NOTION_RICH_TEXT_LIMIT)}}]}


def _prop_text(prop: dict) -> str:
    """Texte brut d'une propriété rich_text (ou '' si absente)."""
    if not prop:
        return ""
    return "".join(t.get("plain_text", "") for t in prop.get("rich_text", []))


def _conv_sig(text: str) -> str:
    """Empreinte de la conversation : ne réécrire le corps que si elle change."""
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def _chunk_text(text: str, limit: int) -> list:
    """Découpe `text` en morceaux d'au plus `limit` unités UTF-16."""
    chunks = []
    rest = text
    while rest:
        head = _truncate_utf16(rest, limit)
        chunks.append(head)
        rest = rest[len(head):]
    return chunks


def _conversation_blocks(full_text: str) -> list:
    """Construit les blocs du corps : un marqueur (heading) + des paragraphes.

    Les messages (séparés par « \\n\\n ») sont regroupés en paragraphes d'au plus
    2000 unités UTF-16 ; un message plus long est lui-même redécoupé.
    """
    blocks = [{
        "object": "block", "type": "heading_2",
        "heading_2": {"rich_text": [{"type": "text", "text": {"content": CONV_MARKER}}]},
    }]

    def paragraph(content):
        return {
            "object": "block", "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": content}}]},
        }

    current = ""
    for msg in (full_text.split("\n\n") if full_text else []):
        candidate = msg if not current else f"{current}\n\n{msg}"
        if len(candidate.encode("utf-16-le")) // 2 <= NOTION_RICH_TEXT_LIMIT:
            current = candidate
        else:
            if current:
                blocks.extend(paragraph(p) for p in _chunk_text(current, NOTION_RICH_TEXT_LIMIT))
            current = msg
    if current:
        blocks.extend(paragraph(p) for p in _chunk_text(current, NOTION_RICH_TEXT_LIMIT))
    return blocks


def _get_all_children(block_id: str) -> list:
    """Tous les blocs enfants d'une page/bloc (paginé)."""
    children, cursor = [], None
    while True:
        path = f"/blocks/{block_id}/children?page_size=100"
        if cursor:
            path += f"&start_cursor={cursor}"
        r = _request("GET", path)
        children.extend(r.get("results", []))
        if not r.get("has_more"):
            break
        cursor = r.get("next_cursor")
    return children


def _sync_conversation_body(page_id: str, full_text: str) -> None:
    """Réécrit l'intégralité de la conversation dans le corps de la page.

    Supprime l'ancienne section du bot (marqueur + blocs suivants) puis réécrit,
    en préservant tout contenu humain situé avant le marqueur.
    """
    children = _get_all_children(page_id)
    marker_idx = None
    for i, b in enumerate(children):
        if b.get("type") == "heading_2":
            txt = "".join(t.get("plain_text", "") for t in b["heading_2"].get("rich_text", []))
            if txt.startswith("💬 Conversation Discord"):
                marker_idx = i
                break
    if marker_idx is not None:
        for b in children[marker_idx:]:
            _request("DELETE", f"/blocks/{b['id']}")

    blocks = _conversation_blocks(full_text)
    for i in range(0, len(blocks), NOTION_CHILDREN_BATCH):
        _request("PATCH", f"/blocks/{page_id}/children", {"children": blocks[i:i + NOTION_CHILDREN_BATCH]})


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


def _build_properties(data: dict, title_prop: str = None) -> dict:
    title_prop = title_prop or _TITLE_PROP
    props: dict = {
        title_prop: {"title": [{"text": {"content": data["nom_du_jeu"]}}]},
    }
    if data.get("source"):
        props["Source"] = _rich_text(data["source"])
    if data.get("date"):
        props["Dernier échange"] = {"date": {"start": data["date"]}}
    if data.get("messages"):
        props["Messages"] = _rich_text(data["messages"])
    if data.get("liens"):
        props["Liens"] = _rich_text(data["liens"])
    if data.get("pieces_jointes"):
        props["Pièces jointes"] = {
            "files": [
                {"name": url.split("/")[-1][:100], "external": {"url": url}}
                for url in data["pieces_jointes"]
            ]
        }
    if data.get("thread_id"):
        props["Thread ID"] = _rich_text(str(data["thread_id"]))
    if data.get("steam_url"):
        props["Lien Steam"] = {"url": data["steam_url"]}
    if data.get("kickstarter"):
        props["Kickstarter"] = {"url": data["kickstarter"]}
    if data.get("pitch_decks"):
        props["Pitch Deck"] = _rich_text("\n".join(data["pitch_decks"]))
    if data.get("exec_docs"):
        props["Exec Doc"] = _rich_text("\n".join(data["exec_docs"]))
    if data.get("youtubes"):
        props["YouTube"] = _rich_text("\n".join(data["youtubes"]))
    if data.get("twitters"):
        props["Twitter"] = _rich_text("\n".join(data["twitters"]))
    if data.get("fathoms"):
        props["Enregistrement fathom"] = _rich_text("\n".join(data["fathoms"]))
    if data.get("drives"):
        props["Drive / Assets"] = _rich_text("\n".join(data["drives"]))
    if data.get("instagrams"):
        props["Instagram"] = _rich_text("\n".join(data["instagrams"]))
    if data.get("canvas"):
        props["Canva"] = _rich_text("\n".join(data["canvas"]))
    if data.get("autres_steam_urls"):
        props["Autres Steam URLs"] = _rich_text(data["autres_steam_urls"])
    if data.get("studio"):
        props["Studio"] = _rich_text(data["studio"])
    if data.get("website_studio"):
        props["Website studio"] = {"url": data["website_studio"]}
    if data.get("description_jeu"):
        props["Description jeux"] = _rich_text(data["description_jeu"])
    if data.get("email"):
        props["Email"] = {"email": data["email"]}
    if data.get("tags"):
        props["Tags"] = {"multi_select": [{"name": t} for t in data["tags"]]}
    if data.get("summary"):
        props["Summary"] = _rich_text(data["summary"])
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

    La conversation intégrale (``messages_full``) est écrite dans le CORPS de la
    page (blocs), sans coupure ; la propriété ``Messages`` n'en garde qu'un aperçu.

    Retourne {"action": "created"|"updated", "id": "<page_id>"}.
    """
    # Conversation intégrale destinée au corps de page (repli sur l'aperçu).
    full_text = data.get("messages_full") or data.get("messages") or ""
    sig = _conv_sig(full_text) if full_text else ""

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
                "property": _TITLE_PROP,
                "title": {"equals": data["nom_du_jeu"]},
            }
        })

    properties = _build_properties(data, _TITLE_PROP)

    if result["results"]:
        page_id = result["results"][0]["id"]
        existing_props = _request("GET", f"/pages/{page_id}").get("properties", {})
        # Corps à réécrire seulement si la conversation a changé (empreinte).
        body_changed = bool(full_text) and _prop_text(existing_props.get("Conv sync")) != sig
        patch = {
            name: value
            for name, value in properties.items()
            if name in ALWAYS_UPDATE or _is_empty(existing_props.get(name, {}))
        }
        if body_changed:
            patch["Conv sync"] = _rich_text(sig)
        if patch:
            _request("PATCH", f"/pages/{page_id}", {"properties": patch})
        if body_changed:
            _sync_conversation_body(page_id, full_text)
        return {"action": "updated", "id": page_id}
    else:
        if full_text:
            properties["Conv sync"] = _rich_text(sig)
        page = _request("POST", "/pages", {
            "parent": {"database_id": DB_ID},
            "properties": properties,
        })
        if full_text:
            _sync_conversation_body(page["id"], full_text)
        return {"action": "created", "id": page["id"]}


def ensure_schema() -> None:
    """Aligne le schéma de la base cible sur ce que le bot écrit, sans toucher
    aux champs humains du CRM.

    - détecte la propriété titre de la base (ex. « Jeu ») et la mémorise ;
    - crée les colonnes gérées manquantes (MANAGED_COLUMNS) ;
    - corrige en rich_text celles qui existeraient avec un autre type (ex. url),
      sinon Notion rejette le push entier (HTTP 400) dès qu'un lien catégorisé
      est présent.
    """
    global _TITLE_PROP
    db = _request("GET", f"/databases/{DB_ID}")
    existing = db.get("properties", {})

    for name, spec in existing.items():
        if spec.get("type") == "title":
            _TITLE_PROP = name
            break

    patch = {}
    for name, spec in MANAGED_COLUMNS.items():
        current = existing.get(name)
        is_rich_text = "rich_text" in spec
        if current is None:
            patch[name] = spec
        elif is_rich_text and current.get("type") != "rich_text":
            patch[name] = spec  # corrige un type divergent (ex. url -> rich_text)
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
        "Summary": _rich_text(summary),
        "Dernier résumé": {"date": {"start": date_str}},
    }})


def trigger_ai_summary(page_id: str, messages_text: str, date_str: str) -> None:
    """Re-écrit Messages pour déclencher le remplissage auto Notion IA sur Summary.

    Notion IA recalcule Summary automatiquement quand sa source (Messages) change.
    Stamp Dernier résumé dans le même appel pour tracker la dernière régénération.
    """
    _request("PATCH", f"/pages/{page_id}", {"properties": {
        "Messages": _rich_text(messages_text),
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
    ensure_schema()  # détecte la propriété titre et crée les colonnes gérées
    result = push_to_notion(TEST_DATA)
    print(f"[{result['action'].upper()}] {result['id']}")
