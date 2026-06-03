"""Configuration pytest : rend les modules de bots/ importables dans les tests.

Comme le bot vit désormais dans bots/, on ajoute ce dossier au sys.path afin que
les tests puissent faire ``import discord_bot`` sans packaging ni installation.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "bots"))
