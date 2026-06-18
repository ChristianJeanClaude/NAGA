"""Logique principale du bot Discord.

Le bot écoute les réactions ajoutées aux messages du canal de scouting. Quand
deux membres réagissent à un message contenant un lien Steam, le bot déclenche
le pipeline de scouting : extraction de l'App ID, agrégation des données
(Steam / SteamSpy), puis création d'une fiche dans Notion.

La déduplication repose sur le cache local (``services.cache``) : un même
message n'est traité qu'une seule fois, même si plusieurs réactions arrivent.
"""

import logging
from datetime import datetime, timezone

import discord
from discord.ext import commands

from config import DISCORD_CHANNEL_ID
from services.cache import is_processed, mark_processed
from services.steam import extract_app_id, fetch_game_data
from services.notion import (
    create_game_page,
    find_existing_page,
    get_page_id,
)
from services.notion_enrich import enrich_game_page
from services.scoring import compute_relevance_score

logger = logging.getLogger(__name__)

intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    logger.info("Bot ready — logged in as %s", bot.user)


@bot.event
async def on_message(message: discord.Message) -> None:
    await bot.process_commands(message)
    # Les messages de commande (ex. "!rescan <url>") sont déjà gérés par
    # process_commands ; ne pas les traiter comme des posts de scouting.
    if message.content.startswith("!"):
        return
    if message.channel.id != DISCORD_CHANNEL_ID:
        return
    if message.author.bot:
        return
    app_id = extract_app_id(message.content)
    if app_id is None:
        return

    existing_url = await find_existing_page(app_id)
    if existing_url:
        # Bot silencieux : aucun message Discord, journalisation console seule.
        logger.info("Jeu déjà scouté (app_id=%s), réactions non ajoutées.", app_id)
        return

    # Only add reactions if game is not already scouted
    for emoji in ["👍", "👎"]:
        await message.add_reaction(emoji)


async def _mark_outreached(app_id: int) -> None:
    """Coche la case "Outreached?" de la fiche Notion pour cet ``app_id``.

    Best-effort : journalise au niveau INFO en cas de succès, WARNING/ERROR en
    cas d'échec. Ne lève jamais.
    """
    try:
        import os

        from dotenv import load_dotenv
        from notion_client import AsyncClient

        from services.notion import get_page_id

        load_dotenv()

        page_id = await get_page_id(app_id)
        if page_id is None:
            logger.warning(
                f"✅ Outreached: page Notion introuvable pour app_id={app_id}"
            )
            return

        notion = AsyncClient(auth=os.environ.get("NOTION_TOKEN", ""))
        await notion.pages.update(
            page_id=page_id,
            properties={"Outreached?": {"checkbox": True}},
        )
        logger.info(f"✅ Outreached mis à jour pour app_id={app_id}")
    except Exception as exc:
        logger.error(f"Erreur Outreached pour app_id={app_id}: {exc}")


@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    """Déclenché à chaque réaction. Pipeline de scouting.

    1. Ignore si le canal n'est pas DISCORD_CHANNEL_ID.
    2. Ignore les réactions du bot lui-même.
    3. Récupère le message et en extrait l'App ID Steam (ignore si absent).
    4. Exige qu'au moins 2 utilisateurs distincts (hors bots) aient réagi.
    5. Ignore si le message a déjà été traité (cache).
    6. Si une fiche Notion existe déjà pour cet App ID, marque comme traité
       et s'arrête (évite les doublons).
    7. Sinon : agrège les données, crée la fiche Notion et marque comme traité.

    Le bot opère silencieusement : aucune sortie Discord, journalisation seule.
    """
    # 1. Filtrer sur le canal de scouting.
    if payload.channel_id != DISCORD_CHANNEL_ID:
        return

    # 2. Ignorer les réactions du bot lui-même.
    if bot.user is not None and payload.user_id == bot.user.id:
        return

    # 3. Récupérer le canal et le message.
    try:
        channel = bot.get_channel(payload.channel_id) or await bot.fetch_channel(
            payload.channel_id
        )
        message = await channel.fetch_message(payload.message_id)
    except discord.NotFound:
        # Thread archivé/supprimé ou message effacé : fetch_message échoue.
        logger.warning(
            "Message %s introuvable dans le canal %s (thread archivé ou "
            "message supprimé), réaction ignorée.",
            payload.message_id,
            payload.channel_id,
        )
        return
    except discord.Forbidden:
        # Permissions insuffisantes (ex. thread archivé sans accès).
        logger.warning(
            "Accès refusé au message %s du canal %s (permissions "
            "insuffisantes), réaction ignorée.",
            payload.message_id,
            payload.channel_id,
        )
        return
    except discord.HTTPException:
        logger.error(
            "Impossible de récupérer le message %s du canal %s",
            payload.message_id,
            payload.channel_id,
            exc_info=True,
        )
        return

    # Réaction ✅ → marquer "Outreached?" dans Notion. Doit s'exécuter AVANT le
    # seuil de 2 réactions : un seul ✅ suffit à déclencher l'Outreach.
    if str(payload.emoji) == "✅":
        # Extraction de l'App ID depuis le contenu ou l'embed.
        app_id = extract_app_id(message.content)
        if app_id is None and message.embeds:
            app_id = extract_app_id(message.embeds[0].url or "")
        if app_id is None:
            return

        await _mark_outreached(app_id)
        return

    app_id = extract_app_id(message.content)
    if app_id is None and message.embeds:
        # Repli sur l'URL de l'embed (ex. suggestions postées par le bot).
        app_id = extract_app_id(message.embeds[0].url or "")
    if app_id is None:
        # Pas de lien Steam dans ce message : rien à scouter.
        return

    # 4. Seuil : au moins 2 utilisateurs distincts (hors bots) doivent avoir réagi.
    unique_user_ids: set[int] = set()
    for reaction in message.reactions:
        async for user in reaction.users():
            if not user.bot:
                unique_user_ids.add(user.id)
    if len(unique_user_ids) < 2:
        return

    # 5. Déduplication locale : ne pas retraiter le même message.
    if await is_processed(payload.message_id, payload.channel_id):
        logger.info(
            "Message %s déjà traité (app_id=%s), ignoré.",
            payload.message_id,
            app_id,
        )
        return

    # 6. Garde anti-doublon : la fiche existe déjà dans Notion.
    existing_url = await find_existing_page(app_id)
    if existing_url:
        await mark_processed(message.id, message.channel.id, app_id)
        return

    # 7. Identité du scout + métadonnées de contexte.
    scouted_by = message.author.display_name
    scouted_at = datetime.now(timezone.utc).isoformat()
    discord_message_url = message.jump_url

    try:
        game = await fetch_game_data(
            app_id=app_id,
            scouted_by=scouted_by,
            scouted_at=scouted_at,
            discord_message_url=discord_message_url,
        )
        game.relevance_score = compute_relevance_score(game)
        await create_game_page(game)
        # Enrichissement best-effort : ne lève jamais (erreurs loguées en interne).
        page_id = await get_page_id(game.app_id)
        if page_id:
            await enrich_game_page(page_id, game)
    except Exception:
        logger.error(
            "Échec du scouting pour app_id=%s (message %s)",
            app_id,
            payload.message_id,
            exc_info=True,
        )
        return

    # Succès : marquer comme traité (journalisation assurée par create_game_page).
    # Le bot reste silencieux après un scouting réussi : aucun embed posté.
    await mark_processed(payload.message_id, payload.channel_id, app_id)
