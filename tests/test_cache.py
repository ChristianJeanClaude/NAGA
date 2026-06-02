"""Tests de services/cache.py — cache SQLite de déduplication.

Chaque test s'exécute contre une base temporaire isolée (``tmp_path``) afin de
ne jamais toucher au fichier réel ``data/cache.db``.
"""

import pytest

from services import cache


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Redirige le cache vers une base SQLite temporaire et jetable."""
    db_dir = tmp_path / "data"
    monkeypatch.setattr(cache, "DB_DIR", db_dir)
    monkeypatch.setattr(cache, "DB_PATH", db_dir / "cache.db")
    return db_dir


async def test_init_db_creates_dir_and_table(temp_db):
    await cache.init_db()
    assert (temp_db / "cache.db").exists()
    # La table existe : une requête ne lève pas d'erreur.
    assert await cache.is_processed(1, 1) is False


async def test_is_processed_false_when_absent(temp_db):
    await cache.init_db()
    assert await cache.is_processed(111, 222) is False


async def test_mark_then_is_processed_true(temp_db):
    await cache.init_db()
    await cache.mark_processed(111, 222, 999)
    assert await cache.is_processed(111, 222) is True


async def test_is_processed_distinguishes_pairs(temp_db):
    await cache.init_db()
    await cache.mark_processed(111, 222, 999)
    # Même message_id mais channel_id différent → pas encore traité.
    assert await cache.is_processed(111, 333) is False


async def test_mark_processed_idempotent(temp_db):
    await cache.init_db()
    await cache.mark_processed(111, 222, 999)
    # Un second appel sur la même paire ne doit pas lever (INSERT OR IGNORE).
    await cache.mark_processed(111, 222, 999)
    assert await cache.is_processed(111, 222) is True
