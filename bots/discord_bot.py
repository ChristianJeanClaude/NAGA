"""Bot Discord en lecture seule : scrape un channel et upsert les jeux dans Notion.

Au démarrage, le bot parcourt tout l'historique du channel configuré, puis écoute
les nouveaux messages en temps réel. Pour chaque message il construit une structure
JSON standardisée (texte, liens, pièces jointes, auteur, date), en déduit le nom du
jeu via le lien Steam présent dans le message, puis upsert dans une base Notion :
recherche par nom de jeu, ajout du message si la page existe, création sinon.

Aucun stockage local : Notion est la seule destination. L'idempotence (pas de doublon
au redémarrage) repose sur un marqueur ``⟦msg:<id>⟧`` écrit dans chaque bloc message.

Dépendances : ``discord.py`` (à installer). Notion est appelé via ``urllib`` (stdlib).

Secrets (jamais hardcodés) :
- ``~/.env.discord`` : DISCORD_TOKEN, DISCORD_CHANNEL_ID
- ``~/.env.notion``  : NOTION_TOKEN, NOTION_PARENT_PAGE_ID
"""

import asyncio
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import discord

# --- Configuration -----------------------------------------------------------

ENV_DISCORD = Path.home() / ".env.discord"
ENV_NOTION = Path.home() / ".env.notion"

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
DB_NAME = "NAGA — Jeux Discord"
# Page fourre-tout pour les messages sans jeu de référence dans leur thread
# (nom aligné sur le salon Discord « Splash Divers »).
DIVERS_NAME = "Splash Divers"
# Propriété rich_text stockant les message_id déjà enregistrés (idempotence).
MESSAGE_IDS_PROP = "Message IDs"
# Propriété rich_text stockant l'id Discord du thread (clé de déduplication).
THREAD_ID_PROP = "Thread ID"

STEAM_API = "https://store.steampowered.com/api/appdetails"

URL_RE = re.compile(r"https?://\S+")
STEAM_APP_RE = re.compile(r"store\.steampowered\.com/app/(\d+)(?:/([^/?\s]+))?")
STEAM_NEWS_RE = re.compile(r"store\.steampowered\.com/news/app/(\d+)")
_STEAM_PREVIEW_LINE_RE = re.compile(r"^(?:Steam|>A_|Release Date|Kickstarter)", re.IGNORECASE)
_CITATION_RE = re.compile(r"^.+ - \S+\nOP$", re.MULTILINE)

# Ancien marqueur d'idempotence (désormais remplacé par la propriété
# « Message IDs »). Conservé uniquement pour le nettoyage par migrate_notion_format.
MSG_MARKER = "⟦msg:{id}⟧"
MSG_MARKER_RE = re.compile(r"⟦msg:(\d+)⟧")


def log(message):
    """Affiche un message d'avancement sur la sortie standard."""
    print(message, flush=True)


def load_env_file(path, required):
    """Parse un fichier ``KEY=VALUE`` ou lit les variables d'environnement système.

    Si le fichier existe, il est parsé (lignes vides et commentaires ``#`` ignorés).
    S'il est absent (Railway, Docker…), on retombe sur ``os.environ``.
    Lève ``KeyError`` si une clé requise est absente. Ne logge jamais les valeurs.
    """
    if path.exists():
        values = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip('"').strip("'")
        source = path.name
    else:
        values = dict(os.environ)
        source = "variables d'environnement système"
        log(f"Fichier {path.name} absent — lecture depuis {source}.")

    missing = [key for key in required if not values.get(key)]
    if missing:
        raise KeyError(f"Clé(s) manquante(s) dans {source} : {', '.join(missing)}")
    return values


# --- Extraction message → JSON standardisé -----------------------------------

def extract_links(text):
    """Retourne la liste des URLs présentes dans le texte."""
    return URL_RE.findall(text or "")


def extract_steam_links(links):
    """Filtre les URLs qui pointent vers une fiche jeu ou une news Steam."""
    return [url for url in links if STEAM_APP_RE.search(url) or STEAM_NEWS_RE.search(url)]


def slug_to_name(slug):
    """Transforme un slug d'URL Steam (``Hollow_Knight``) en nom lisible."""
    return re.sub(r"[_+]", " ", slug).strip() if slug else ""


async def resolve_game(steam_url):
    """Déduit ``{app_id, name, url}`` d'une URL Steam.

    Le nom est récupéré via l'API publique Steam ``appdetails``. En cas d'échec
    réseau ou de réponse invalide, on retombe sur le slug de l'URL.
    """
    match = STEAM_APP_RE.search(steam_url)
    if match:
        app_id = match.group(1)
        fallback_name = slug_to_name(match.group(2)) or f"Steam app {app_id}"
        store_url = steam_url
    else:
        match = STEAM_NEWS_RE.search(steam_url)
        app_id = match.group(1)
        fallback_name = f"Steam app {app_id}"
        store_url = f"https://store.steampowered.com/app/{app_id}/"

    def _fetch():
        try:
            url = f"{STEAM_API}?appids={app_id}&l=english"
            req = urllib.request.Request(url, headers={"User-Agent": "naga-discord-bot"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            entry = payload.get(app_id, {})
            if entry.get("success"):
                return entry.get("data") or {}
        except (urllib.error.URLError, ValueError, KeyError, TimeoutError):
            pass
        return {}

    data = await asyncio.to_thread(_fetch)
    result = {
        "app_id": int(app_id),
        "name": data.get("name") or fallback_name,
        "url": store_url,
    }
    developers = data.get("developers") or []
    if developers:
        result["developer"] = developers[0]
    website = (data.get("website") or "").strip()
    if website:
        result["website"] = website
    short_desc = (data.get("short_description") or "").strip()
    if short_desc:
        result["short_description"] = short_desc
    return result


def parse_message_ids(text):
    """Texte de la propriété « Message IDs » → ensemble d'id (séparés par espaces)."""
    return set(text.split())


def format_message_ids(ids):
    """Ensemble d'id → texte trié, séparé par des espaces (stocké dans Notion).

    Les id Discord ont la même longueur, donc le tri lexicographique == chronologique.
    """
    return " ".join(sorted(ids))


def format_timestamp(iso):
    """ISO 8601 → ``DD.MM.YYYY HH:MM`` (conserve l'heure du message, UTC Discord)."""
    try:
        return datetime.fromisoformat(iso).strftime("%d.%m.%Y %H:%M")
    except ValueError:
        return iso


def build_message_record(message):
    """Construit la structure JSON standardisée d'un message Discord."""
    text = message.content or ""
    links = extract_links(text)
    steam_links = extract_steam_links(links)

    return {
        "message_id": str(message.id),
        "channel_id": str(message.channel.id),
        "timestamp": message.created_at.isoformat(),
        "author": {
            "id": str(message.author.id),
            "name": str(message.author),
            "display_name": message.author.display_name,
        },
        "text": text,
        "links": links,
        "steam_links": steam_links,
        "attachments": [
            {
                "filename": att.filename,
                "url": att.url,
                "content_type": att.content_type,
                "size": att.size,
            }
            for att in message.attachments
        ],
        "game": None,
    }


# Limite Notion d'un rich_text : le module Leads ne découpe pas, on tronque.
LEAD_TEXT_LIMIT = 2000


def clean_message_text(text, author_display_name, timestamp):
    """Nettoie le texte d'un message Discord et le préfixe [DD/MM/YYYY HH:MM - auteur].

    Supprime URLs, blocs preview Steam, patterns de citation, lignes vides multiples.
    Retourne toujours une chaîne (au minimum le préfixe) même si le texte est vide.
    """
    cleaned = re.sub(r"https?://\S+", "", text or "")

    paragraphs = re.split(r"\n{2,}", cleaned)
    kept = [
        p for p in paragraphs
        if not any(_STEAM_PREVIEW_LINE_RE.search(line) for line in p.splitlines())
    ]
    cleaned = "\n\n".join(kept)

    cleaned = _CITATION_RE.sub("", cleaned)

    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()

    try:
        date_str = datetime.fromisoformat(timestamp).strftime("%d/%m/%Y %H:%M")
    except (ValueError, TypeError):
        date_str = timestamp or ""

    return f"[{date_str} - {author_display_name}] {cleaned}".strip()


KICKSTARTER_RE = re.compile(r"kickstarter\.com", re.IGNORECASE)
PITCH_RE = re.compile(r"docs\.google\.com|pitch|\.pdf", re.IGNORECASE)

def _sort_liens(liens):
    """Répartit les liens dédupliqués par catégorie (première correspondance).

    Retourne (steam_url, kickstarter, pitch_deck, autres_steam, autres).
    Le premier lien Steam va dans steam_url ; les suivants dans autres_steam
    (ils ne tombent pas dans autres).
    """
    steam_url = kickstarter = pitch_deck = None
    autres_steam = []
    autres = []
    for url in dict.fromkeys(liens):
        if STEAM_APP_RE.search(url):
            if steam_url is None:
                steam_url = url
            else:
                autres_steam.append(url)
        elif m := STEAM_NEWS_RE.search(url):
            if steam_url is None:
                steam_url = f"https://store.steampowered.com/app/{m.group(1)}/"
            autres.append(url)
        elif KICKSTARTER_RE.search(url):
            if kickstarter is None:
                kickstarter = url
        elif pitch_deck is None and PITCH_RE.search(url):
            pitch_deck = url
        else:
            autres.append(url)
    return steam_url, kickstarter, pitch_deck, autres_steam, autres


def build_lead_payload(title, messages, liens, pieces, date, thread_id=None, tags=None, game=None):
    """Construit le dict attendu par notion_leads.push_to_notion pour un thread.

    Agrège la conversation du thread ; déduplique et trie les liens par catégorie ;
    tronque « messages » et « liens » à la limite Notion.
    """
    steam_url, kickstarter, pitch_deck, autres_steam, autres = _sort_liens(liens)
    payload = {
        "nom_du_jeu": title,
        "source": "Discord #leads",
        "date": date,
        "messages": "\n\n".join(messages)[:LEAD_TEXT_LIMIT],
        "liens": "\n".join(autres)[:LEAD_TEXT_LIMIT],
        "pieces_jointes": list(dict.fromkeys(pieces)),
    }
    if tags:
        payload["tags"] = tags
    if steam_url:
        payload["steam_url"] = steam_url
    if autres_steam:
        payload["autres_steam_urls"] = "\n".join(autres_steam)[:LEAD_TEXT_LIMIT]
    if kickstarter:
        payload["kickstarter"] = kickstarter
    if pitch_deck:
        payload["pitch_deck"] = pitch_deck
    if thread_id is not None:
        payload["thread_id"] = str(thread_id)
    if game:
        if game.get("developer"):
            payload["studio"] = game["developer"]
        if game.get("website"):
            payload["website_studio"] = game["website"]
        if game.get("short_description"):
            payload["description_jeu"] = game["short_description"][:LEAD_TEXT_LIMIT]
    return payload


# --- Client Notion (urllib via asyncio.to_thread) ----------------------------

class NotionError(RuntimeError):
    """Erreur d'appel à l'API Notion (sans donnée sensible dans le message)."""


class NotionClient:
    """Client minimal de l'API Notion construit sur ``urllib`` (stdlib).

    Toutes les méthodes qui font des appels réseau sont ``async`` : l'I/O
    bloquant urllib est exécuté dans un thread via ``asyncio.to_thread`` afin
    de ne pas bloquer la boucle d'événements discord.py.
    """

    def __init__(self, token, config_id):
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        }
        # L'id configuré peut désigner soit la page parente (on crée la base
        # dessous), soit directement une database existante (on l'utilise).
        self._config_id = config_id
        self.database_id = None
        self._title_prop = "Name"
        self._schema = set()
        # Cache des id enregistrés par page (le bot est l'unique rédacteur).
        self._recorded_cache = {}

    async def _request(self, method, path, payload=None):
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(
            f"{NOTION_API}{path}", data=data, headers=self._headers, method=method
        )

        def _do():
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                raise NotionError(f"Notion {method} {path} → HTTP {exc.code}") from None
            except urllib.error.URLError as exc:
                raise NotionError(f"Notion {method} {path} → {exc.reason}") from None

        return await asyncio.to_thread(_do)

    # -- Database ------------------------------------------------------------

    async def find_or_create_database(self):
        """Résout la base cible et mémorise son id.

        L'id configuré (``NOTION_PARENT_PAGE_ID``) est accepté qu'il désigne une
        page parente ou directement une database :
        - si c'est déjà une database, on l'utilise telle quelle ;
        - sinon on cherche ``DB_NAME`` dans le workspace, ou on la crée sous la page.
        """
        # Cas 1 : l'id configuré est lui-même une database → on l'adopte.
        try:
            existing = await self._request("GET", f"/databases/{self._config_id}")
        except NotionError:
            existing = None
        if existing is not None:
            self._adopt_database(existing)
            log(f"Base Notion utilisée : {self._database_title(existing)}")
            return self.database_id

        # Cas 2 : l'id configuré est une page → on cherche une base DB_NAME existante…
        result = await self._request(
            "POST",
            "/search",
            {"query": DB_NAME, "filter": {"value": "database", "property": "object"}},
        )
        for item in result.get("results", []):
            if self._database_title(item) == DB_NAME:
                self._adopt_database(item)
                log(f"Base Notion existante trouvée : {DB_NAME}")
                return self.database_id

        # … ou on la crée sous la page configurée.
        created = await self._request(
            "POST",
            "/databases",
            {
                "parent": {"type": "page_id", "page_id": self._config_id},
                "title": [{"type": "text", "text": {"content": DB_NAME}}],
                "properties": {
                    "Name": {"title": {}},
                    "Steam URL": {"url": {}},
                    "Steam App ID": {"number": {}},
                    "Première vue": {"date": {}},
                    "Dernier message": {"date": {}},
                    "Channel": {"rich_text": {}},
                    THREAD_ID_PROP: {"rich_text": {}},
                },
            },
        )
        self._adopt_database(created)
        log(f"Base Notion créée : {DB_NAME}")
        return self.database_id

    def _adopt_database(self, db):
        """Mémorise l'id, la propriété titre et les colonnes réellement présentes."""
        self.database_id = db["id"]
        properties = db.get("properties", {})
        self._schema = set(properties)
        for name, spec in properties.items():
            if spec.get("type") == "title":
                self._title_prop = name
                break

    @staticmethod
    def _database_title(db):
        return "".join(t.get("plain_text", "") for t in db.get("title", []))

    # -- Pages jeu -----------------------------------------------------------

    async def find_game_page(self, thread_id):
        """Retourne l'id de la page dont « Thread ID » vaut ``thread_id``, ou ``None``."""
        result = await self._request(
            "POST",
            f"/databases/{self.database_id}/query",
            {
                "filter": {"property": THREAD_ID_PROP, "rich_text": {"equals": str(thread_id)}},
                "page_size": 1,
            },
        )
        results = result.get("results", [])
        return results[0]["id"] if results else None

    async def create_page(self, title, record, thread_id, game=None):
        """Crée une page (jeu ou « Divers ») dans la base et retourne son id.

        Seules les propriétés réellement présentes dans le schéma sont écrites,
        afin de fonctionner aussi avec une database existante au schéma différent.
        Les colonnes Steam ne sont renseignées que si un ``game`` est fourni.
        """
        properties = {self._title_prop: {"title": [{"text": {"content": title}}]}}
        optional = {
            "Première vue": {"date": {"start": record["timestamp"]}},
            "Dernier message": {"date": {"start": record["timestamp"]}},
            "Channel": {"rich_text": [{"text": {"content": record["channel_id"]}}]},
            THREAD_ID_PROP: {"rich_text": [{"text": {"content": str(thread_id)}}]},
        }
        if game is not None:
            optional["Steam URL"] = {"url": game["url"]}
            optional["Steam App ID"] = {"number": game["app_id"]}
        for prop_name, value in optional.items():
            if prop_name in self._schema:
                properties[prop_name] = value

        page = await self._request(
            "POST",
            "/pages",
            {"parent": {"database_id": self.database_id}, "properties": properties},
        )
        return page["id"]

    async def touch_last_message(self, page_id, timestamp):
        """Met à jour « Dernier message » si la colonne existe dans la base."""
        if "Dernier message" not in self._schema:
            return
        await self._request(
            "PATCH",
            f"/pages/{page_id}",
            {"properties": {"Dernier message": {"date": {"start": timestamp}}}},
        )

    async def update_page_title(self, page_id, title):
        """Met à jour le titre d'une page Notion."""
        await self._request(
            "PATCH",
            f"/pages/{page_id}",
            {"properties": {self._title_prop: {"title": [{"text": {"content": title}}]}}},
        )

    # -- Idempotence via la propriété « Message IDs » -----------------------

    async def ensure_message_ids_property(self):
        """Crée la propriété rich_text « Message IDs » si la base ne l'a pas."""
        if MESSAGE_IDS_PROP in self._schema:
            return
        await self._request(
            "PATCH",
            f"/databases/{self.database_id}",
            {"properties": {MESSAGE_IDS_PROP: {"rich_text": {}}}},
        )
        self._schema.add(MESSAGE_IDS_PROP)

    async def ensure_thread_id_property(self):
        """Crée la propriété rich_text « Thread ID » si la base ne l'a pas."""
        if THREAD_ID_PROP in self._schema:
            return
        await self._request(
            "PATCH",
            f"/databases/{self.database_id}",
            {"properties": {THREAD_ID_PROP: {"rich_text": {}}}},
        )
        self._schema.add(THREAD_ID_PROP)

    async def get_recorded_ids(self, page_id):
        """Ensemble des message_id déjà enregistrés sur la page (avec cache)."""
        if page_id in self._recorded_cache:
            return self._recorded_cache[page_id]
        page = await self._request("GET", f"/pages/{page_id}")
        prop = page.get("properties", {}).get(MESSAGE_IDS_PROP, {})
        text = "".join(t.get("plain_text", "") for t in prop.get("rich_text", []))
        ids = parse_message_ids(text)
        self._recorded_cache[page_id] = ids
        return ids

    async def set_recorded_ids(self, page_id, ids):
        """Écrit l'ensemble des message_id dans la propriété « Message IDs »."""
        await self._request(
            "PATCH",
            f"/pages/{page_id}",
            {"properties": {
                MESSAGE_IDS_PROP: {"rich_text": _rich_text(format_message_ids(ids))}
            }},
        )
        self._recorded_cache[page_id] = set(ids)

    async def add_recorded_id(self, page_id, recorded, message_id):
        """Ajoute un message_id à la propriété (à partir de l'ensemble connu)."""
        await self.set_recorded_ids(page_id, recorded | {message_id})

    # -- Blocs message ------------------------------------------------------

    async def append_message_block(self, page_id, record):
        """Ajoute le message en un bloc lisible « DD.MM.YYYY HH:MM AUTEUR : texte ».

        Aucun marqueur technique : l'idempotence est portée par la propriété
        « Message IDs » de la page.
        """
        when = format_timestamp(record["timestamp"])
        author = record["author"]["display_name"]
        text = record["text"] or "(aucun texte)"

        rich_text = (
            _rich_text(f"{when} {author} : ", {"bold": True})
            + _rich_text(text)
        )
        children = [_paragraph(rich_text)]
        for url in record["links"]:
            children.append(_bullet(url))
        for att in record["attachments"]:
            children.append(_bullet(f"📎 {att['filename']} — {att['url']}"))

        await self._request("PATCH", f"/blocks/{page_id}/children", {"children": children})


def _rich_text(content, annotations=None):
    """Segments rich_text Notion, découpés à 2000 caractères (limite API)."""
    segments = []
    for start in range(0, len(content), 2000):
        segment = {"type": "text", "text": {"content": content[start:start + 2000]}}
        if annotations:
            segment["annotations"] = annotations
        segments.append(segment)
    return segments


def _paragraph(rich_text):
    return {"object": "block", "type": "paragraph",
            "paragraph": {"rich_text": rich_text}}


def _bullet(content):
    return {"object": "block", "type": "bulleted_list_item",
            "bulleted_list_item": {"rich_text": _rich_text(content)}}


# --- Bot Discord -------------------------------------------------------------

async def all_forum_threads(channel):
    """Tous les threads d'un forum (actifs + archivés), dédupliqués par id.

    Un forum n'a pas d'historique propre : ses messages vivent dans ses posts.
    On combine les threads actifs récupérés via l'API du serveur
    (``guild.active_threads`` — plus fiable que le cache au démarrage), le cache
    du channel et les threads archivés publics, afin de n'en manquer aucun.
    """
    threads = {}
    try:
        for thread in await channel.guild.active_threads():
            if thread.parent_id == channel.id:
                threads[thread.id] = thread
    except discord.DiscordException as exc:
        log(f"active_threads indisponible ({exc}), repli sur le cache.")
    for thread in channel.threads:
        threads[thread.id] = thread
    async for thread in channel.archived_threads(limit=None):
        threads[thread.id] = thread
    return list(threads.values())


class NagaScraperBot(discord.Client):
    """Bot lecture seule : scrape l'historique puis écoute le channel cible."""

    def __init__(self, channel_id, notion, push_lead=None, **kwargs):
        super().__init__(**kwargs)
        self._channel_id = channel_id
        self._notion = notion
        # Push vers la 2e base Notion (Leads de Djoundounda) ; None = désactivé.
        self._push_lead = push_lead
        # Conversation agrégée par thread pour la base Leads :
        # {thread_id: {"title", "messages":[…], "liens":[…], "pieces":[…], "date"}}
        self._thread_leads = {}

    async def on_ready(self):
        log(f"Connecté en tant que {self.user} — scraping de l'historique…")
        channel = self.get_channel(self._channel_id)
        if channel is None:
            try:
                channel = await self.fetch_channel(self._channel_id)
            except discord.DiscordException:
                log(f"Channel {self._channel_id} introuvable ou inaccessible.")
                await self.close()
                return

        count = 0
        if isinstance(channel, discord.ForumChannel):
            threads = await all_forum_threads(channel)
            log(f"{len(threads)} thread(s) de forum à scraper.")
            for thread in threads:
                count += await self._scrape_thread(thread)
        else:
            count += await self._scrape_thread(channel)
        log(f"Historique traité : {count} message(s). Écoute en temps réel active.")

    async def _scrape_thread(self, channel):
        """Parcourt tout l'historique d'un thread/salon et retourne le nombre traité."""
        count = 0
        async for message in channel.history(limit=None, oldest_first=True):
            await self.process_message(message)
            count += 1
        # Conversation du thread complète : on pousse une seule fois vers Leads.
        self._push_thread_lead(channel.id)
        return count

    def _is_target(self, channel):
        """Vrai si le salon est le channel cible ou un thread enfant de ce channel."""
        return (
            channel.id == self._channel_id
            or getattr(channel, "parent_id", None) == self._channel_id
        )

    async def on_message(self, message):
        if message.author.bot or not self._is_target(message.channel):
            return
        await self.process_message(message)
        self._push_thread_lead(message.channel.id)

    async def on_thread_create(self, thread):
        # Nouveau post de forum : le message d'ouverture peut ne pas déclencher
        # on_message, on le traite donc explicitement (l'idempotence évite les doublons).
        if thread.parent_id != self._channel_id:
            return
        async for message in thread.history(limit=None, oldest_first=True):
            await self.process_message(message)
        self._push_thread_lead(thread.id)

    async def on_thread_update(self, before, after):
        if after.parent_id != self._channel_id:
            return

        name_changed = before.name != after.name
        before_tag_ids = {t.id for t in getattr(before, "applied_tags", [])}
        after_tag_ids = {t.id for t in getattr(after, "applied_tags", [])}
        tags_changed = before_tag_ids != after_tag_ids

        if not name_changed and not tags_changed:
            return

        if name_changed:
            page_id = await self._notion.find_game_page(str(after.id))
            if page_id is not None:
                try:
                    await self._notion.update_page_title(page_id, after.name)
                    log(f"Page renommée : « {before.name} » → « {after.name} ».")
                except NotionError as exc:
                    log(f"Erreur renommage thread {after.id} : {exc}")

        if tags_changed and self._push_lead is not None:
            new_tags = [t.name for t in getattr(after, "applied_tags", [])]
            acc = self._thread_leads.get(after.id)
            if acc is not None:
                acc["tags"] = new_tags
                self._push_thread_lead(after.id)
                log(f"Tags mis à jour pour « {after.name} » : {new_tags}.")

    async def process_message(self, message):
        """Enregistre TOUS les messages dans Notion.

        - message dans un thread du forum : page nommée d'après le titre du thread
          (créée si besoin), avec ou sans lien Steam ;
        - message hors-thread : page « Splash Divers ».
        """
        try:
            record = build_message_record(message)
            log(json.dumps(record, ensure_ascii=False))

            channel = message.channel
            in_thread = (
                isinstance(channel, discord.Thread)
                and channel.parent_id == self._channel_id
            )
            thread_id = str(channel.id)
            if record["steam_links"]:
                record["game"] = await resolve_game(record["steam_links"][0])

            if in_thread:
                title = channel.name
                # Alimente la conversation agrégée (base Leads), même si le
                # message est déjà enregistré côté base principale.
                thread_tags = [t.name for t in getattr(channel, "applied_tags", [])]
                self._accumulate_lead(channel.id, title, record, tags=thread_tags)
            else:
                title = DIVERS_NAME

            page_id = await self._notion.find_game_page(thread_id)
            if page_id is None:
                page_id = await self._notion.create_page(title, record, thread_id, record["game"])
                log(f"Page créée : {title}")
            else:
                await self._notion.touch_last_message(page_id, record["timestamp"])

            recorded = await self._notion.get_recorded_ids(page_id)
            if record["message_id"] in recorded:
                log(f"Message {record['message_id']} déjà enregistré, ignoré.")
                return

            await self._notion.append_message_block(page_id, record)
            await self._notion.add_recorded_id(page_id, recorded, record["message_id"])
            log(f"Message {record['message_id']} ajouté à « {title} ».")
        except (NotionError, discord.DiscordException) as exc:
            # On logge l'id et le type d'erreur, jamais le contenu du message.
            log(f"Erreur sur message {message.id} : {exc}")

    def _accumulate_lead(self, thread_id, title, record, tags=None):
        """Ajoute un message à la conversation agrégée du thread (base Leads)."""
        acc = self._thread_leads.setdefault(
            thread_id, {"title": title, "messages": [], "liens": [], "pieces": [], "tags": [], "game": None}
        )
        acc["title"] = title
        if tags is not None:
            acc["tags"] = tags
        if acc["game"] is None and record.get("game"):
            acc["game"] = record["game"]
        acc["messages"].append(
            clean_message_text(record["text"], record["author"]["display_name"], record["timestamp"])
        )
        acc["liens"].extend(record["links"])
        acc["pieces"].extend(att["url"] for att in record["attachments"])
        acc["date"] = record["timestamp"]

    def _push_thread_lead(self, thread_id):
        """Pousse la conversation agrégée d'un thread vers la base Leads."""
        if self._push_lead is None:
            return
        acc = self._thread_leads.get(thread_id)
        if not acc:
            return
        data = build_lead_payload(
            acc["title"], acc["messages"], acc["liens"], acc["pieces"], acc.get("date"),
            thread_id=thread_id,
            tags=acc.get("tags") or None,
            game=acc.get("game"),
        )
        try:
            self._push_lead(data)
            log(f"Lead poussé : « {acc['title']} ».")
        except Exception as exc:  # noqa: BLE001 — isole tout échec du module tiers
            log(f"Échec push Leads pour « {acc['title']} » : {exc}")


async def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    discord_env = load_env_file(ENV_DISCORD, ["DISCORD_TOKEN", "DISCORD_CHANNEL_ID"])
    notion_env = load_env_file(ENV_NOTION, ["NOTION_TOKEN", "NOTION_PARENT_PAGE_ID"])

    notion = NotionClient(notion_env["NOTION_TOKEN"], notion_env["NOTION_PARENT_PAGE_ID"])
    await notion.find_or_create_database()
    await notion.ensure_message_ids_property()
    await notion.ensure_thread_id_property()

    # Seconde base Notion (Leads de Djoundounda). Le module lit son token dans
    # NOTION_TOKEN_LEADS à l'import : on renseigne d'abord l'environnement.
    push_lead = None
    leads_token = notion_env.get("NOTION_TOKEN_LEADS")
    if leads_token:
        os.environ["NOTION_TOKEN_LEADS"] = leads_token
        import notion_leads
        push_lead = notion_leads.push_to_notion
        log("Push vers la base Leads activé.")
    else:
        log("NOTION_TOKEN_LEADS absent : push vers la base Leads désactivé.")

    intents = discord.Intents.default()
    intents.message_content = True
    bot = NagaScraperBot(
        int(discord_env["DISCORD_CHANNEL_ID"]), notion,
        push_lead=push_lead, intents=intents,
    )
    async with bot:
        await bot.start(discord_env["DISCORD_TOKEN"])


if __name__ == "__main__":
    asyncio.run(main())
