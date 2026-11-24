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
import urllib.parse
import urllib.request
import uuid

import notion_rate

NOTION_TOKEN = os.environ.get("NOTION_TOKEN_LEADS", "")
# Id de la base Leads : configurable via NOTION_DB_LEADS_ID (~/.env.notion),
# avec l'ancienne valeur codée en dur comme repli rétrocompatible.
DB_ID = os.environ.get("NOTION_DB_LEADS_ID") or "37277389-df89-809e-a356-c242c5e43cbb"
NOTION_VERSION = "2022-06-28"
BASE_URL = "https://api.notion.com/v1"


def _request(method: str, path: str, body: dict | None = None) -> dict:
    """Appel JSON à l'API Notion, sous le throttle partagé et avec nouvel essai
    sur 429 / 5xx (voir notion_rate)."""
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

    def _send():
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())

    try:
        return notion_rate.call_with_retry(_send, method)
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        # Les messages d'erreur Notion citent souvent l'id de la page : masqué.
        try:
            notion_error = json.loads(body)
            message = notion_rate.redact_ids(notion_error.get("message"))
            print(f"Erreur Notion {e.code} : {notion_error.get('code')} — {message}")
        except json.JSONDecodeError:
            print(f"Erreur HTTP {e.code} : {notion_rate.redact_ids(body)}")
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


def _tail_utf16(content: str, limit: int) -> str:
    """Garde la FIN de ``content`` : ses ``limit`` dernières unités UTF-16.

    Pendant de _truncate_utf16 pour la colonne Messages, où ce sont les
    échanges les plus récents qui comptent. Une demi-paire de substitution en
    tête de coupe est jetée.
    """
    encoded = content.encode("utf-16-le")
    if len(encoded) <= limit * 2:
        return content
    return encoded[-limit * 2:].decode("utf-16-le", errors="ignore")


def _rich_text(content: str) -> dict:
    """Construit une propriété rich_text en tronquant à la limite Notion (2000 unités UTF-16)."""
    return {"rich_text": [{"text": {"content": _truncate_utf16(content, NOTION_RICH_TEXT_LIMIT)}}]}


def _rich_text_tail(content: str) -> dict:
    """Propriété rich_text qui garde la fin du texte (colonne Messages)."""
    return {"rich_text": [{"text": {"content": _tail_utf16(content, NOTION_RICH_TEXT_LIMIT)}}]}


def _prop_text(prop: dict) -> str:
    """Texte brut d'une propriété rich_text (ou '' si absente)."""
    if not prop:
        return ""
    return "".join(t.get("plain_text", "") for t in prop.get("rich_text", []))


# Version de la disposition des blocs du corps. À incrémenter quand le FORMAT
# change (pas juste le contenu) : l'empreinte intègre cette version, ce qui force
# la réécriture de tous les corps existants une fois, dans la nouvelle disposition.
#   v1 : paragraphes groupés (≤2000 car.)   v2 : un bloc paragraphe par message
_BODY_FORMAT_VERSION = "2"


def _conv_sig(text: str) -> str:
    """Empreinte (conversation + version de format) : ne réécrire le corps que si l'une change."""
    return hashlib.md5(f"{_BODY_FORMAT_VERSION}\x00{text}".encode("utf-8")).hexdigest()


def _chunk_text(text: str, limit: int) -> list:
    """Découpe `text` en morceaux d'au plus `limit` unités UTF-16."""
    chunks = []
    rest = text
    while rest:
        head = _truncate_utf16(rest, limit)
        chunks.append(head)
        rest = rest[len(head):]
    return chunks


def _conversation_blocks(messages: list) -> list:
    """Construit les blocs du corps : un marqueur (heading) + un paragraphe par message.

    Chaque message devient son propre bloc paragraphe (séparation naturelle entre
    blocs Notion). Un message dépassant 2000 unités UTF-16 est redécoupé en
    plusieurs blocs. Les messages vides sont ignorés.
    """
    blocks = [{
        "object": "block", "type": "heading_2",
        "heading_2": {"rich_text": [{"type": "text", "text": {"content": CONV_MARKER}}]},
    }]
    for msg in (messages or []):
        msg = (msg or "").strip()
        if not msg:
            continue
        for piece in _chunk_text(msg, NOTION_RICH_TEXT_LIMIT):
            blocks.append({
                "object": "block", "type": "paragraph",
                "paragraph": {"rich_text": [{"type": "text", "text": {"content": piece}}]},
            })
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


def _sync_conversation_body(page_id: str, messages: list) -> None:
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

    blocks = _conversation_blocks(messages)
    for i in range(0, len(blocks), NOTION_CHILDREN_BATCH):
        _request("PATCH", f"/blocks/{page_id}/children", {"children": blocks[i:i + NOTION_CHILDREN_BATCH]})


def _is_empty(prop: dict) -> bool:
    """Retourne True si une propriété Notion ne contient aucune valeur."""
    ptype = prop.get("type")
    if ptype in ("rich_text", "title", "files"):
        return not prop.get(ptype)
    if ptype in ("date", "url", "number", "select", "email"):
        return prop.get(ptype) is None
    if ptype == "multi_select":
        return not prop.get("multi_select")
    return False


# --- Pièces jointes : téléversement dans Notion ------------------------------
#
# Les URLs cdn.discordapp.com sont signées (ex=/is=/hm=) et expirent : la
# colonne « Pièces jointes » finissait en liste de liens morts. Le bot
# télécharge donc le fichier depuis Discord (URL fraîche, obtenue au scrape)
# et le téléverse dans Notion (File Upload API, envoi en une partie). Repli si
# le fichier est trop lourd, d'un type refusé par Notion, ou si l'envoi
# échoue : lien PERMANENT vers le message Discord qui porte la pièce jointe.

ATTACHMENTS_PROP = "Pièces jointes"
# Envoi en une partie : 20 Mo max côté Notion (5 Mio sur un workspace gratuit,
# auquel cas Notion refuse l'envoi et le repli s'applique).
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
# Notion refuse plus de 100 éléments dans un tableau de propriété.
NOTION_FILES_LIMIT = 100
DISCORD_CDN_HOSTS = ("cdn.discordapp.com", "media.discordapp.net")


def _is_discord_cdn(url: str) -> bool:
    return urllib.parse.urlparse(url or "").hostname in DISCORD_CDN_HOSTS


def _filename_from_url(url: str) -> str:
    """Nom du fichier d'après le chemin de l'URL, sans la query de signature."""
    return urllib.parse.unquote(urllib.parse.urlparse(url or "").path.rsplit("/", 1)[-1]) or "fichier"


def _piece(piece) -> dict:
    """Une pièce jointe sous forme de dict, qu'on reçoive une URL nue ou un dict."""
    return dict(piece) if isinstance(piece, dict) else {"url": piece}


def _attachment_name(piece: dict) -> str:
    return (piece.get("filename") or _filename_from_url(piece.get("url")))[:100]


def _fallback_entry(piece: dict) -> dict:
    """Entrée « lien externe » : le message Discord (permanent) plutôt que le CDN (expire)."""
    link = piece.get("jump_url") or piece.get("url")
    return {"name": _attachment_name(piece), "external": {"url": link}}


def _attachments_stale(prop: dict) -> bool:
    """Vrai si la colonne ne contient que des liens CDN Discord écrits par l'ancien bot.

    Ces liens expirent : la colonne est alors réécrite (une fois), avec des
    fichiers téléversés. Un fichier ajouté à la main (type « file ») ou un lien
    vers un message Discord n'est jamais remplacé.
    """
    files = prop.get("files") or []
    return bool(files) and all(
        f.get("type") == "external" and _is_discord_cdn((f.get("external") or {}).get("url"))
        for f in files
    )


def _attachments_writable(prop: dict) -> bool:
    """La colonne peut être (ré)écrite : vide, ou seulement des liens CDN périmés."""
    return _is_empty(prop) or _attachments_stale(prop)


def _multipart_body(filename: str, content_type: str, content: bytes, boundary: str) -> bytes:
    """Corps multipart/form-data du champ « file » attendu par Notion."""
    safe_name = filename.replace('"', "'").replace("\r", " ").replace("\n", " ")
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{safe_name}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode("utf-8")
    return head + content + f"\r\n--{boundary}--\r\n".encode("ascii")


def _download_discord_attachment(url: str, max_bytes: int):
    """Contenu d'une pièce jointe du CDN Discord, ou None (trop lourde, inaccessible).

    Seuls les hôtes du CDN Discord sont téléchargés : jamais une URL arbitraire.
    """
    if not _is_discord_cdn(url):
        return None
    req = urllib.request.Request(url, headers={"User-Agent": "naga-discord-bot"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            content = resp.read(max_bytes + 1)
    except (urllib.error.URLError, TimeoutError, ValueError):
        return None
    return content if len(content) <= max_bytes else None


def _send_file_upload(upload_id: str, filename: str, content_type: str, content: bytes) -> None:
    """Étape 2 de l'envoi : le fichier lui-même, en multipart (hors JSON)."""
    boundary = uuid.uuid4().hex
    req = urllib.request.Request(
        f"{BASE_URL}/file_uploads/{upload_id}/send",
        data=_multipart_body(filename, content_type, content, boundary),
        method="POST",
        headers={
            "Authorization": f"Bearer {NOTION_TOKEN}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )

    def _send():
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read())

    notion_rate.call_with_retry(_send, "POST file_upload")


def _upload_attachment(piece: dict):
    """Téléverse une pièce jointe Discord dans Notion. Retourne l'entrée « files » ou None."""
    size = piece.get("size")
    if size and size > MAX_UPLOAD_BYTES:
        return None
    content = _download_discord_attachment(piece.get("url"), MAX_UPLOAD_BYTES)
    if content is None:
        return None
    filename = _attachment_name(piece)
    content_type = (piece.get("content_type") or "application/octet-stream").split(";", 1)[0].strip()
    created = _request("POST", "/file_uploads", {"filename": filename, "content_type": content_type})
    _send_file_upload(created["id"], filename, content_type, content)
    # À attacher dans l'heure, sinon Notion expire l'envoi : c'est fait juste après.
    return {"type": "file_upload", "file_upload": {"id": created["id"]}, "name": filename}


def _write_attachments(page_id: str, pieces: list, thread_id=None) -> None:
    """Écrit « Pièces jointes » dans un appel à part, sans jamais faire échouer le push.

    Chaque fichier est téléversé ; à défaut, lien vers le message Discord. Si
    Notion refuse l'écriture avec des fichiers téléversés, on réessaie avec les
    seuls liens de repli.
    """
    pieces = [_piece(p) for p in pieces][:NOTION_FILES_LIMIT]
    entries, uploaded = [], 0
    for piece in pieces:
        try:
            entry = _upload_attachment(piece)
        except Exception:  # noqa: BLE001 — une pièce ratée ne doit pas bloquer les autres
            entry = None
        if entry is not None:
            uploaded += 1
        entries.append(entry or _fallback_entry(piece))

    def _patch(files):
        _request("PATCH", f"/pages/{page_id}", {"properties": {ATTACHMENTS_PROP: {"files": files}}})

    try:
        _patch(entries)
    except urllib.error.URLError:
        try:
            if not uploaded:
                raise
            _patch([_fallback_entry(p) for p in pieces])
            uploaded = 0
        except urllib.error.URLError:
            print(f"Pièces jointes non écrites (thread {thread_id}).")
            return
    print(f"Pièces jointes (thread {thread_id}) : {uploaded} téléversée(s), "
          f"{len(pieces) - uploaded} en lien vers le message Discord.")


def _update_patch(properties: dict, existing_props: dict, only_empty: bool = False) -> dict:
    """Propriétés à écrire sur une page existante.

    Les champs de ALWAYS_UPDATE sont toujours réécrits ; les autres seulement si
    la colonne est vide côté Notion (ex. Email : une adresse saisie à la main
    n'est jamais écrasée par celle trouvée dans le fil). ``only_empty`` (fiche
    manuelle adoptée) ne remplit que les colonnes vides, ALWAYS_UPDATE compris.
    """
    return {
        name: value
        for name, value in properties.items()
        if (name in ALWAYS_UPDATE and not only_empty) or _is_empty(existing_props.get(name, {}))
    }


def _normalize_title(title: str) -> str:
    """Titre comparable : sans casse ni espaces (« Battle Only » == « battleonly »)."""
    return "".join((title or "").split()).casefold()


def _page_title(page: dict) -> str:
    """Texte brut de la propriété titre d'une page (quel que soit son nom)."""
    for prop in page.get("properties", {}).values():
        if prop.get("type") == "title":
            return "".join(t.get("plain_text", "") for t in prop.get("title", []))
    return ""


def _pick_adoptable(pages: list, title: str):
    """Id de l'unique page dont le titre correspond à ``title``, sinon None.

    Plusieurs homonymes : on ne devine pas, le bot crée sa propre fiche.
    """
    wanted = _normalize_title(title)
    matches = [p["id"] for p in pages if _normalize_title(_page_title(p)) == wanted]
    return matches[0] if len(matches) == 1 else None


def _find_adoptable_page(title: str):
    """Fiche créée à la main (Thread ID vide) portant le même titre que le fil.

    Notion ne filtre pas un titre sans casse ni espaces : on liste les fiches
    sans Thread ID et on compare côté Python.
    """
    pages, cursor = [], None
    while True:
        query = {
            "filter": {"property": "Thread ID", "rich_text": {"is_empty": True}},
            "page_size": 100,
        }
        if cursor:
            query["start_cursor"] = cursor
        r = _request("POST", f"/databases/{DB_ID}/query", query)
        pages.extend(r.get("results", []))
        if not r.get("has_more"):
            break
        cursor = r.get("next_cursor")
    return _pick_adoptable(pages, title)


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
        # Aperçu : la FIN de la conversation (échanges récents), pas le début.
        props["Messages"] = _rich_text_tail(data["messages"])
    if data.get("liens"):
        props["Liens"] = _rich_text(data["liens"])
    if data.get("pieces_jointes"):
        # Forme de repli (liens) ; push_to_notion écrit la colonne à part, avec
        # les fichiers téléversés (voir _write_attachments).
        props[ATTACHMENTS_PROP] = {
            "files": [_fallback_entry(_piece(p)) for p in data["pieces_jointes"]][:NOTION_FILES_LIMIT]
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

    Si aucune page ne porte ce Thread ID mais qu'une seule fiche créée à la main
    (Thread ID vide) a le même titre, elle est adoptée : le bot y écrit le
    Thread ID et ne remplit que les colonnes vides, au lieu de créer un doublon.

    Retourne {"action": "created"|"updated"|"adopted", "id": "<page_id>"}.
    """
    # Conversation intégrale destinée au corps de page (repli sur l'aperçu).
    full_text = data.get("messages_full") or data.get("messages") or ""
    messages_list = data.get("messages_list")
    if messages_list is None:
        messages_list = [full_text] if full_text else []
    sig = _conv_sig(full_text) if full_text else ""

    adopted = False
    if data.get("thread_id"):
        result = _request("POST", f"/databases/{DB_ID}/query", {
            "filter": {
                "property": "Thread ID",
                "rich_text": {"equals": str(data["thread_id"])},
            }
        })
        page_id = result["results"][0]["id"] if result["results"] else None
        if page_id is None:
            page_id = _find_adoptable_page(data["nom_du_jeu"])
            adopted = page_id is not None
    else:
        result = _request("POST", f"/databases/{DB_ID}/query", {
            "filter": {
                "property": _TITLE_PROP,
                "title": {"equals": data["nom_du_jeu"]},
            }
        })
        page_id = result["results"][0]["id"] if result["results"] else None

    properties = _build_properties(data, _TITLE_PROP)
    # Pièces jointes écrites à part, après téléversement : un échec d'envoi ne
    # doit pas faire rejeter le reste du push.
    has_pieces = properties.pop(ATTACHMENTS_PROP, None) is not None
    thread_id = data.get("thread_id")

    if page_id is not None:
        existing_props = _request("GET", f"/pages/{page_id}").get("properties", {})
        # Corps à réécrire seulement si la conversation a changé (empreinte).
        body_changed = bool(full_text) and _prop_text(existing_props.get("Conv sync")) != sig
        patch = _update_patch(properties, existing_props, only_empty=adopted)
        if body_changed:
            patch["Conv sync"] = _rich_text(sig)
        if patch:
            _request("PATCH", f"/pages/{page_id}", {"properties": patch})
        if body_changed:
            _sync_conversation_body(page_id, messages_list)
        if has_pieces and _attachments_writable(existing_props.get(ATTACHMENTS_PROP, {})):
            _write_attachments(page_id, data["pieces_jointes"], thread_id)
        return {"action": "adopted" if adopted else "updated", "id": page_id}
    else:
        if full_text:
            properties["Conv sync"] = _rich_text(sig)
        page = _request("POST", "/pages", {
            "parent": {"database_id": DB_ID},
            "properties": properties,
        })
        if full_text:
            _sync_conversation_body(page["id"], messages_list)
        if has_pieces:
            _write_attachments(page["id"], data["pieces_jointes"], thread_id)
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
    _request("PATCH", f"/pages/{page_id}", {"properties": _summary_trigger_properties(messages_text, date_str)})


def _summary_trigger_properties(messages_text: str, date_str: str) -> dict:
    """Propriétés écrites par trigger_ai_summary.

    Messages garde la FIN de la conversation, comme au push : _rich_text
    coupait par le début, si bien que sur les fils longs la colonne montrait
    les 2000 premiers caractères et s'arrêtait en plein mot.
    """
    return {
        "Messages": _rich_text_tail(messages_text),
        "Dernier résumé": {"date": {"start": date_str}},
    }


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
