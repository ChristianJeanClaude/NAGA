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

import aiohttp
import discord
from discord.ext import commands

from config import (
    DISCORD_CHANNEL_ID,
    DISCORD_CMD_CHANNEL_ID,
    DISCORD_SCOUT_LOG_CHANNEL_ID,
    DISCORD_SUGGEST_CHANNEL_ID,
    NOTION_DATABASE_ID,
)
from services.cache import is_processed, mark_processed
from services.steam import HEADERS, extract_app_id, fetch_game_data
from services.notion import (
    client,
    create_game_page,
    find_existing_page,
    get_all_app_ids,
    get_page_id,
)
from services.notion_enrich import enrich_game_page
from services.notion_update import update_game_page
from services.scoring import compute_relevance_score
from services.suggest import get_naga_profile, search_steam_suggestions

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
        embed = discord.Embed(
            title="⚠️ Jeu déjà scouté",
            description="Ce jeu est déjà dans la base Notion.",
            color=0xFFA500,
        )
        embed.add_field(name="Steam App ID", value=str(app_id), inline=True)
        view = discord.ui.View()
        view.add_item(
            discord.ui.Button(
                label="Voir sur Notion",
                url=existing_url,
                style=discord.ButtonStyle.link,
            )
        )
        await message.channel.send(embed=embed, view=view)
        return

    # Only add reactions if game is not already scouted
    for emoji in ["👍", "👎", "🔥", "❤️"]:
        await message.add_reaction(emoji)


async def _send_to_scout_log(embed, view=None):
    """Publie un embed dans le canal de log de scouting (feed d'audit).

    Toute panne du canal de log est journalisée et avalée pour ne jamais
    impacter le pipeline de scouting principal.
    """
    try:
        channel = bot.get_channel(DISCORD_SCOUT_LOG_CHANNEL_ID) or \
                  await bot.fetch_channel(DISCORD_SCOUT_LOG_CHANNEL_ID)
        await channel.send(embed=embed, view=view)
    except Exception as e:
        logger.error(f"Failed to send to scout log channel: {e}")


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
        notion_url = await create_game_page(game)
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
    await mark_processed(payload.message_id, payload.channel_id, app_id)

    # Couleur dynamique selon le relevance score (neutre si 0/None).
    score = game.relevance_score
    if not score:
        color = 0x1B2838  # Steam dark blue (neutre)
    elif score >= 70:
        color = 0x57F287  # vert
    elif score >= 50:
        color = 0xFEE75C  # jaune
    else:
        color = 0xED4245  # rouge

    embed = discord.Embed(
        title=game.name,
        url=game.steam_url,
        description=game.short_description[:200] + "..."
        if len(game.short_description) > 200
        else game.short_description,
        color=color,
    )
    embed.add_field(name="Developer", value=game.developer or "N/A", inline=True)
    embed.add_field(
        name="Genres",
        value=", ".join(game.genres) if game.genres else "N/A",
        inline=True,
    )
    # Champs post-lancement : masqués s'ils sont vides (jeu pas encore sorti).
    if game.review_score:
        embed.add_field(name="Review Score", value=game.review_score, inline=True)
    if game.owners_estimate:
        embed.add_field(name="Owners", value=game.owners_estimate, inline=True)
    if game.peak_ccu:
        embed.add_field(name="Peak CCU", value=str(game.peak_ccu), inline=True)
    embed.add_field(
        name="Release Date",
        value=game.release_date or "Coming soon",
        inline=True,
    )
    if game.relevance_score:
        embed.add_field(
            name="Relevance Score", value=str(game.relevance_score), inline=True
        )
    embed.add_field(name="Scouted By", value=game.scouted_by or "N/A", inline=True)
    embed.set_image(
        url=f"https://cdn.akamai.steamstatic.com/steam/apps/{game.app_id}/header.jpg"
    )
    embed.set_footer(text="NAGA Scout Bot • Scouted ✅")

    view = discord.ui.View()
    view.add_item(
        discord.ui.Button(
            label="Voir sur Notion",
            url=notion_url,
            style=discord.ButtonStyle.link,
        )
    )

    await _send_to_scout_log(embed, view)


@bot.command(name="rescan")
async def rescan(ctx, url: str = None):
    """Usage: !rescan <steam_url>

    Récupère des données fraîches depuis Steam et met à jour la fiche Notion.
    """
    if url is None:
        await ctx.send("Usage: `!rescan <steam_url>`")
        return

    app_id = extract_app_id(url)
    if app_id is None:
        await ctx.send("❌ URL Steam invalide.")
        return

    page_id = await get_page_id(app_id)
    if page_id is None:
        await ctx.send(f"❌ App ID `{app_id}` introuvable dans Notion.")
        return

    try:
        # Le scout d'origine est préservé : update_game_page exclut "Scouted By".
        scouted_by = ctx.author.display_name
        scouted_at = datetime.now(timezone.utc).isoformat()
        discord_message_url = ctx.message.jump_url

        game = await fetch_game_data(
            app_id, scouted_by, scouted_at, discord_message_url
        )
        game.relevance_score = compute_relevance_score(game)
        notion_url = await update_game_page(page_id, game)

        embed = discord.Embed(
            title=f"🔄 {game.name}",
            description="Métriques mises à jour avec succès.",
            color=0x57F287,  # green
        )
        embed.add_field(
            name="Review Score", value=game.review_score or "N/A", inline=True
        )
        embed.add_field(
            name="Owners", value=game.owners_estimate or "N/A", inline=True
        )
        embed.add_field(
            name="Peak CCU",
            value=str(game.peak_ccu) if game.peak_ccu else "N/A",
            inline=True,
        )
        embed.set_footer(text="NAGA Scout Bot • Rescan ✅")

        view = discord.ui.View()
        view.add_item(
            discord.ui.Button(
                label="Voir sur Notion",
                url=notion_url,
                style=discord.ButtonStyle.link,
            )
        )
        await ctx.send(embed=embed, view=view)
    except Exception:
        logger.error("Échec du rescan pour app_id=%s", app_id, exc_info=True)
        await ctx.send("❌ Échec du rescan.")


@bot.command(name="suggest")
@commands.check(lambda ctx: ctx.channel.id == DISCORD_CMD_CHANNEL_ID)
@commands.cooldown(1, 300, commands.BucketType.guild)
async def suggest(ctx):
    """Analyse la base Notion et suggère des jeux indés à venir.

    Réservée au salon de commandes ; poste les résultats dans le salon de
    suggestions.
    """
    suggest_channel = bot.get_channel(DISCORD_SUGGEST_CHANNEL_ID)
    if suggest_channel is None:
        await ctx.send("❌ Salon de suggestions introuvable.")
        return

    try:
        await ctx.send("🔍 Analyse de la base en cours...")

        # Profil NAGA (genres/tags les plus fréquents) + App IDs déjà connus.
        profile = await get_naga_profile(client, NOTION_DATABASE_ID)
        known_ids = await get_all_app_ids()

        async with aiohttp.ClientSession(headers=HEADERS) as session:
            suggestions = await search_steam_suggestions(
                profile["genres"],
                profile["tags"],
                known_ids,
                session,
                hashtags=profile.get("hashtags", []),
            )

        if not suggestions:
            await ctx.send("😕 Aucune suggestion trouvée pour le moment.")
            return

        await suggest_channel.send(
            f"🎮 **{len(suggestions)} suggestions basées sur le profil NAGA**\n"
            f"Genres : {', '.join(profile['genres'][:3]) or 'N/A'}\n"
            f"Tags : {', '.join(profile['tags'][:3]) or 'N/A'}\n"
            f"Hashtags du jour : {', '.join(profile.get('hashtags', [])[:3]) or 'N/A'}"
        )

        for game in suggestions:
            embed = discord.Embed(
                title=game["name"],
                url=game["steam_url"],
                description=game.get("short_description", "")[:200],
                color=0x5865F2,  # Discord blurple
            )
            embed.add_field(
                name="Genres",
                value=", ".join(game.get("genres", [])) or "N/A",
                inline=True,
            )
            embed.add_field(
                name="Release Date",
                value=game.get("release_date", "N/A"),
                inline=True,
            )
            embed.set_thumbnail(
                url=f"https://cdn.akamai.steamstatic.com/steam/apps/{game['app_id']}/header.jpg"
            )
            embed.set_footer(text="NAGA Scout Bot • Suggestion 💡")

            view = discord.ui.View()
            view.add_item(
                discord.ui.Button(
                    label="Voir sur Steam",
                    url=game["steam_url"],
                    style=discord.ButtonStyle.link,
                )
            )
            suggestion_msg = await suggest_channel.send(embed=embed, view=view)
            for emoji in ["👍", "👎", "🔥", "❤️"]:
                await suggestion_msg.add_reaction(emoji)

        # Pas de confirmation redondante si on est déjà dans le bon salon.
        if ctx.channel.id != suggest_channel.id:
            await ctx.send(
                f"✅ {len(suggestions)} suggestions postées dans {suggest_channel.mention}"
            )
    except Exception:
        logger.error("Échec de la commande !suggest", exc_info=True)
        await ctx.send("❌ Échec de la génération des suggestions.")


@suggest.error
async def suggest_error(ctx, error):
    if isinstance(error, commands.CommandOnCooldown):
        await ctx.send(f"⏳ Réessaie dans {error.retry_after:.0f}s.")


@bot.command(name="scout")
@commands.check(lambda ctx: ctx.channel.id == DISCORD_CMD_CHANNEL_ID)
async def scout(ctx):
    """Déclenche manuellement le ScoutingJob."""
    await ctx.send("🔍 ScoutingJob en cours...")
    try:
        from scouting.job import ScoutingJob
        job = ScoutingJob()
        await job.run()
        job.close()
        await ctx.send("✅ ScoutingJob terminé.")
    except Exception as exc:
        logger.error(f"ScoutingJob manuel — ÉCHEC : {exc}", exc_info=True)
        await ctx.send(f"❌ Échec : {exc}")


@scout.error
async def scout_error(ctx, error):
    if isinstance(error, commands.CheckFailure):
        pass  # Silent — wrong channel
