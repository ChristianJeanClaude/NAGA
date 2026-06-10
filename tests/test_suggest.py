"""Tests de services/suggest.py — parsing des tags SteamSpy (sans réseau).

Les appels HTTP sont simulés par une fausse session ``aiohttp`` : aucun appel
réseau réel n'est effectué.
"""

from services.suggest import (
    PRIORITY_HASHTAGS,
    get_steamspy_tag_ids,
    get_todays_hashtags,
)


class _FakeResponse:
    """Réponse aiohttp factice utilisable comme gestionnaire de contexte async."""

    def __init__(self, payload):
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def raise_for_status(self) -> None:
        pass

    async def json(self, content_type=None):
        return self._payload


class _FakeSession:
    """Session aiohttp factice : ``get`` renvoie toujours le même payload."""

    def __init__(self, payload):
        self._payload = payload

    def get(self, url, params=None):
        return _FakeResponse(self._payload)


async def test_get_steamspy_tag_ids_nested_dict():
    session = _FakeSession(
        {
            "Indie": {"id": 492, "count": 1000},
            "Roguelite": {"id": 1716, "count": 500},
        }
    )
    assert await get_steamspy_tag_ids(session) == {"Indie": 492, "Roguelite": 1716}


async def test_get_steamspy_tag_ids_numeric_format():
    # Format alternatif : la valeur est directement numérique (pas de dict).
    session = _FakeSession({"Indie": 492, "Roguelite": 1716})
    assert await get_steamspy_tag_ids(session) == {"Indie": 492, "Roguelite": 1716}


async def test_get_steamspy_tag_ids_skips_malformed_entries():
    session = _FakeSession(
        {
            "Indie": {"id": 492},
            "NoId": {"count": 10},  # dict sans "id"
            "BadType": "not a number",  # valeur ni dict ni int
        }
    )
    assert await get_steamspy_tag_ids(session) == {"Indie": 492}


async def test_get_steamspy_tag_ids_non_dict_payload_returns_empty():
    session = _FakeSession(["unexpected", "list"])
    assert await get_steamspy_tag_ids(session) == {}


def test_get_todays_hashtags_always_includes_priority():
    result = get_todays_hashtags()
    for tag in PRIORITY_HASHTAGS:
        assert tag in result


def test_get_todays_hashtags_max_10():
    assert len(get_todays_hashtags()) <= 10


def test_get_todays_hashtags_no_duplicates():
    result = get_todays_hashtags()
    assert len(result) == len(set(result))
