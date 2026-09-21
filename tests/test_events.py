"""Tests des votes 👍/👎 de bot/events.py (sans Discord ni Notion réels)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot import events
from config import DISCORD_CHANNEL_ID

APP_ID = 1234567
BOT = SimpleNamespace(id=1, bot=True)
ALICE = SimpleNamespace(id=10, bot=False)
BOB = SimpleNamespace(id=11, bot=False)
CARL = SimpleNamespace(id=12, bot=False)


class FakeReaction:
    def __init__(self, emoji, users):
        self.emoji = emoji
        self._users = users

    async def users(self):
        for user in self._users:
            yield user


def make_message(reactions):
    """Message du canal de scouting ; ``reactions`` = {emoji: [users]}."""
    return SimpleNamespace(
        id=42,
        content=f"https://store.steampowered.com/app/{APP_ID}/Some_Game/",
        embeds=[],
        reactions=[FakeReaction(e, u) for e, u in reactions.items()],
        jump_url="https://discord.com/channels/1/1000/42",
        channel=SimpleNamespace(id=DISCORD_CHANNEL_ID),
        author=SimpleNamespace(display_name="scout"),
    )


@pytest.fixture
def pipeline(monkeypatch):
    """Remplace Discord, Notion, Steam et le cache par des mocks."""
    mocks = SimpleNamespace(
        is_processed=AsyncMock(return_value=False),
        mark_processed=AsyncMock(),
        find_existing_page=AsyncMock(return_value=None),
        fetch_game_data=AsyncMock(return_value=SimpleNamespace(app_id=APP_ID)),
        create_game_page=AsyncMock(),
        get_page_id=AsyncMock(return_value=None),
        archive_rejected_page=AsyncMock(return_value=1),
    )
    for name, mock in vars(mocks).items():
        monkeypatch.setattr(events, name, mock)
    monkeypatch.setattr(events, "compute_relevance_score", lambda game: 0)

    def use_message(message):
        channel = SimpleNamespace(fetch_message=AsyncMock(return_value=message))
        monkeypatch.setattr(events.bot, "get_channel", lambda _id: channel)

    mocks.use_message = use_message
    return mocks


async def react(emoji, user=BOB):
    await events.on_raw_reaction_add(
        SimpleNamespace(
            channel_id=DISCORD_CHANNEL_ID,
            user_id=user.id,
            message_id=42,
            emoji=emoji,
        )
    )


async def test_un_pouce_et_un_pouce_en_bas_ne_cree_pas_de_fiche(pipeline):
    # Le cas du bug : 👍 affiche 2 (bot + Alice), Bob a mis 👎.
    pipeline.use_message(make_message({"👍": [BOT, ALICE], "👎": [BOT, BOB]}))
    await react("👎")
    await react("👍", ALICE)
    pipeline.create_game_page.assert_not_awaited()


async def test_autre_emoji_ne_compte_pas(pipeline):
    pipeline.use_message(make_message({"👍": [BOT, ALICE], "🔥": [BOB]}))
    await react("🔥")
    await react("👍", ALICE)
    pipeline.create_game_page.assert_not_awaited()


async def test_deux_pouces_humains_creent_la_fiche(pipeline):
    pipeline.use_message(make_message({"👍": [BOT, ALICE, BOB], "👎": [BOT]}))
    await react("👍")
    pipeline.create_game_page.assert_awaited_once()
    pipeline.mark_processed.assert_awaited_once_with(42, DISCORD_CHANNEL_ID, APP_ID)


async def test_pouce_avec_teint_compte(pipeline):
    pipeline.use_message(make_message({"👍": [BOT, ALICE], "👍🏽": [BOB]}))
    await react("👍🏽")
    pipeline.create_game_page.assert_awaited_once()


async def test_deux_pouces_en_bas_archivent_la_fiche(pipeline):
    message = make_message({"👍": [BOT, ALICE, CARL], "👎": [BOT, ALICE, BOB]})
    pipeline.use_message(message)
    await react("👎")
    pipeline.archive_rejected_page.assert_awaited_once_with(APP_ID, message.jump_url)
    pipeline.create_game_page.assert_not_awaited()


async def test_un_seul_pouce_en_bas_n_archive_pas(pipeline):
    pipeline.use_message(make_message({"👍": [BOT], "👎": [BOT, BOB]}))
    await react("👎")
    pipeline.archive_rejected_page.assert_not_awaited()


async def test_jeu_rejete_n_est_pas_cree_par_des_pouces_tardifs(pipeline):
    pipeline.use_message(
        make_message({"👍": [BOT, ALICE, CARL], "👎": [BOT, BOB, ALICE]})
    )
    await react("👍", CARL)
    pipeline.create_game_page.assert_not_awaited()


async def test_reactions_du_bot_ignorees(pipeline, monkeypatch):
    monkeypatch.setattr(type(events.bot), "user", property(lambda self: BOT))
    pipeline.use_message(make_message({"👍": [BOT, ALICE, BOB]}))
    await react("👍", BOT)
    pipeline.create_game_page.assert_not_awaited()
