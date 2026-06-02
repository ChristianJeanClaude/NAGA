"""Service de cache local (aiosqlite).

Maintient une base SQLite locale qui enregistre les messages Discord déjà
traités, afin d'éviter qu'un même message soit traité deux fois (par exemple
lorsqu'une 3ᵉ réaction arrive après que le scouting a déjà été déclenché).

La déduplication se fait sur la paire ``(message_id, channel_id)``, qui sert
de clé primaire de la table ``processed_messages``.
"""

from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

DB_DIR = Path("data")
DB_PATH = DB_DIR / "cache.db"

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS processed_messages (
    message_id   INTEGER NOT NULL,
    channel_id   INTEGER NOT NULL,
    app_id       INTEGER NOT NULL,
    processed_at TEXT NOT NULL,   -- ISO 8601 timestamp
    PRIMARY KEY (message_id, channel_id)
)
"""


async def init_db() -> None:
    """Crée le dossier ``data/`` et la table si nécessaire."""
    DB_DIR.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(_CREATE_TABLE)
        await db.commit()


async def is_processed(message_id: int, channel_id: int) -> bool:
    """Indique si la paire ``(message_id, channel_id)`` est déjà enregistrée."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM processed_messages "
            "WHERE message_id = ? AND channel_id = ?",
            (message_id, channel_id),
        ) as cursor:
            return await cursor.fetchone() is not None


async def mark_processed(message_id: int, channel_id: int, app_id: int) -> None:
    """Enregistre un message comme traité.

    Idempotent : un appel répété sur la même paire ``(message_id, channel_id)``
    ne lève pas d'erreur et conserve l'enregistrement existant.
    """
    processed_at = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO processed_messages "
            "(message_id, channel_id, app_id, processed_at) "
            "VALUES (?, ?, ?, ?)",
            (message_id, channel_id, app_id, processed_at),
        )
        await db.commit()
