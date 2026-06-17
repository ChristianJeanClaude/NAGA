"""Tests de tracking/outreach_poller.py — persistance du dernier check (sans réseau).

Le fichier d'horodatage est redirigé vers ``tmp_path`` afin de ne jamais
toucher au fichier réel ``db/last_outreach_check.txt``.
"""

from datetime import datetime, timezone, timedelta

import pytest

from tracking import outreach_poller


@pytest.fixture
def temp_check_file(tmp_path, monkeypatch):
    """Redirige LAST_CHECK_FILE vers un fichier temporaire et jetable."""
    path = tmp_path / "last_outreach_check.txt"
    monkeypatch.setattr(outreach_poller, "LAST_CHECK_FILE", str(path))
    return path


async def test_get_last_check_file_missing(temp_check_file):
    # Le fichier n'existe pas → fallback à ~1h dans le passé.
    before = datetime.now(timezone.utc) - timedelta(hours=1)
    result = await outreach_poller._get_last_check()
    after = datetime.now(timezone.utc) - timedelta(hours=1)

    assert result.tzinfo is not None
    # Le fallback se situe entre les deux bornes "il y a 1h" (à la seconde près).
    assert before - timedelta(seconds=5) <= result <= after + timedelta(seconds=5)


async def test_save_and_load_last_check(temp_check_file):
    dt = datetime(2026, 6, 17, 10, 30, 0, tzinfo=timezone.utc)
    await outreach_poller._save_last_check(dt)

    assert temp_check_file.exists()
    loaded = await outreach_poller._get_last_check()
    assert loaded == dt
