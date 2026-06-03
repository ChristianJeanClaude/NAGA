"""Tests des fonctions pures de discord_bot (aucun appel réseau).

Conformément aux contraintes du projet, ces tests n'effectuent aucun appel HTTP :
on ne teste que l'extraction de liens, le parsing de slug Steam et la construction
du record JSON à partir d'un faux message (stub).
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import discord_bot


# --- extract_links / extract_steam_links -------------------------------------

def test_extract_links_multiple():
    text = "voir https://store.steampowered.com/app/367520/Hollow_Knight/ et https://x.io/y"
    links = discord_bot.extract_links(text)
    assert links == [
        "https://store.steampowered.com/app/367520/Hollow_Knight/",
        "https://x.io/y",
    ]


def test_extract_links_vide():
    assert discord_bot.extract_links("") == []
    assert discord_bot.extract_links(None) == []


def test_extract_steam_links_filtre():
    links = [
        "https://store.steampowered.com/app/367520/Hollow_Knight/",
        "https://example.com",
        "https://store.steampowered.com/app/413150",
    ]
    steam = discord_bot.extract_steam_links(links)
    assert steam == [
        "https://store.steampowered.com/app/367520/Hollow_Knight/",
        "https://store.steampowered.com/app/413150",
    ]


def test_extract_steam_links_aucune():
    assert discord_bot.extract_steam_links(["https://itch.io/jeu"]) == []


# --- slug_to_name ------------------------------------------------------------

def test_slug_to_name_underscores():
    assert discord_bot.slug_to_name("Hollow_Knight") == "Hollow Knight"


def test_slug_to_name_plus_et_vide():
    assert discord_bot.slug_to_name("Stardew+Valley") == "Stardew Valley"
    assert discord_bot.slug_to_name("") == ""
    assert discord_bot.slug_to_name(None) == ""


# --- STEAM_APP_RE ------------------------------------------------------------

def test_steam_app_re_capture_id_et_slug():
    m = discord_bot.STEAM_APP_RE.search(
        "https://store.steampowered.com/app/367520/Hollow_Knight/"
    )
    assert m.group(1) == "367520"
    assert m.group(2) == "Hollow_Knight"


def test_steam_app_re_sans_slug():
    m = discord_bot.STEAM_APP_RE.search("https://store.steampowered.com/app/413150")
    assert m.group(1) == "413150"
    assert m.group(2) is None


# --- MSG_MARKER (idempotence) ------------------------------------------------

def test_msg_marker_roundtrip():
    marker = discord_bot.MSG_MARKER.format(id="123456789")
    found = discord_bot.MSG_MARKER_RE.search(f"{marker} Alice · 2026-01-01")
    assert found.group(1) == "123456789"


# --- format_timestamp --------------------------------------------------------

def test_format_timestamp_iso_utc():
    assert discord_bot.format_timestamp("2026-06-01T08:14:23.051000+00:00") == \
        "01.06.2026 08:14"


def test_format_timestamp_sans_microsecondes():
    assert discord_bot.format_timestamp("2026-12-25T19:05:00+00:00") == \
        "25.12.2026 19:05"


def test_format_timestamp_invalide_renvoie_entree():
    assert discord_bot.format_timestamp("pas-une-date") == "pas-une-date"


# --- parse/format Message IDs (idempotence) ----------------------------------

def test_parse_message_ids_basique():
    assert discord_bot.parse_message_ids("111 222 333") == {"111", "222", "333"}


def test_parse_message_ids_vide():
    assert discord_bot.parse_message_ids("") == set()
    assert discord_bot.parse_message_ids("   ") == set()


def test_format_message_ids_trie():
    # Tri lexicographique (= chronologique pour des id de même longueur).
    assert discord_bot.format_message_ids({"333", "111", "222"}) == "111 222 333"


def test_format_message_ids_vide():
    assert discord_bot.format_message_ids(set()) == ""


def test_parse_format_roundtrip():
    ids = {"1510919427539861544", "1510946976269734030"}
    assert discord_bot.parse_message_ids(discord_bot.format_message_ids(ids)) == ids


# --- build_lead_payload (base Leads) -----------------------------------------

def test_build_lead_payload_complet():
    payload = discord_bot.build_lead_payload(
        "Tabula",
        ["Premier message", "Deuxième message"],
        ["https://store.steampowered.com/app/1", "https://kickstarter.com/x"],
        ["https://cdn.discord/a.png"],
        "2026-06-01T08:14:23+00:00",
    )
    assert payload == {
        "nom_du_jeu": "Tabula",
        "source": "Discord #leads",
        "date": "2026-06-01T08:14:23+00:00",
        "messages": "Premier message\n\nDeuxième message",
        "liens": "",
        "pieces_jointes": ["https://cdn.discord/a.png"],
        "steam_url": "https://store.steampowered.com/app/1",
        "kickstarter": "https://kickstarter.com/x",
    }


def test_build_lead_payload_dedup_liens_et_pieces():
    payload = discord_bot.build_lead_payload(
        "Jeu",
        ["m"],
        ["https://a", "https://a", "https://b"],
        ["https://p", "https://p"],
        "2026-06-01",
    )
    assert payload["liens"] == "https://a\nhttps://b"
    assert payload["pieces_jointes"] == ["https://p"]


def test_build_lead_payload_troncature_2000():
    long_msg = "x" * 5000
    payload = discord_bot.build_lead_payload("Jeu", [long_msg], [], [], "2026-06-01")
    assert len(payload["messages"]) == discord_bot.LEAD_TEXT_LIMIT


def test_build_lead_payload_vide():
    payload = discord_bot.build_lead_payload("Jeu", [], [], [], "2026-06-01")
    assert payload["messages"] == ""
    assert payload["liens"] == ""
    assert payload["pieces_jointes"] == []
    assert "statut" not in payload
    assert "steam_url" not in payload
    assert "kickstarter" not in payload


# --- build_message_record ----------------------------------------------------

def _fake_message():
    """Construit un faux message Discord minimal (stub, sans réseau)."""
    # str(author) doit renvoyer le tag complet ; on enveloppe SimpleNamespace.
    author = _Stringable("alice#0001", id=42, display_name="Alice")
    attachment = SimpleNamespace(
        filename="screenshot.png",
        url="https://cdn.discord/att/screenshot.png",
        content_type="image/png",
        size=2048,
    )
    channel = SimpleNamespace(id=1510918770426904586)
    return SimpleNamespace(
        id=999,
        channel=channel,
        created_at=datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc),
        author=author,
        content="Découvrez https://store.steampowered.com/app/367520/Hollow_Knight/",
        attachments=[attachment],
    )


class _Stringable(SimpleNamespace):
    def __init__(self, as_str, **kwargs):
        super().__init__(**kwargs)
        self._as_str = as_str

    def __str__(self):
        return self._as_str


def test_build_message_record_complet():
    record = discord_bot.build_message_record(_fake_message())

    assert record["message_id"] == "999"
    assert record["channel_id"] == "1510918770426904586"
    assert record["timestamp"] == "2026-05-01T12:00:00+00:00"
    assert record["author"] == {
        "id": "42",
        "name": "alice#0001",
        "display_name": "Alice",
    }
    assert record["steam_links"] == [
        "https://store.steampowered.com/app/367520/Hollow_Knight/"
    ]
    assert record["attachments"] == [
        {
            "filename": "screenshot.png",
            "url": "https://cdn.discord/att/screenshot.png",
            "content_type": "image/png",
            "size": 2048,
        }
    ]
    assert record["game"] is None


def test_build_message_record_sans_steam():
    msg = _fake_message()
    msg.content = "Juste un commentaire sans lien"
    msg.attachments = []
    record = discord_bot.build_message_record(msg)
    assert record["links"] == []
    assert record["steam_links"] == []
    assert record["attachments"] == []


# --- clean_message_text -------------------------------------------------------

_TS = "2026-06-01T08:14:23+00:00"
_PREFIX = "[01/06/2026 08:14 - Alice]"


def test_clean_message_text_prefix():
    result = discord_bot.clean_message_text("Bonjour !", "Alice", _TS)
    assert result.startswith(_PREFIX)
    assert "Bonjour !" in result


def test_clean_message_text_texte_vide():
    assert discord_bot.clean_message_text("", "Alice", _TS) == _PREFIX


def test_clean_message_text_texte_none():
    assert discord_bot.clean_message_text(None, "Alice", _TS) == _PREFIX


def test_clean_message_text_supprime_urls():
    text = "Voir ce jeu https://store.steampowered.com/app/1/ c'est cool"
    result = discord_bot.clean_message_text(text, "Alice", _TS)
    assert "https://" not in result
    assert "c'est cool" in result


def test_clean_message_text_supprime_bloc_steam():
    text = "Message normal\n\nSteam\nTitre du jeu\nRelease Date: 2025\n\nSuite du message"
    result = discord_bot.clean_message_text(text, "Alice", _TS)
    assert "Steam" not in result
    assert "Release Date" not in result
    assert "Suite du message" in result
    assert "Message normal" in result


def test_clean_message_text_supprime_bloc_release_date():
    text = "Début\n\nRelease Date: Q1 2026\nDéveloppeur: XYZ\n\nFin"
    result = discord_bot.clean_message_text(text, "Alice", _TS)
    assert "Release Date" not in result
    assert "Fin" in result


def test_clean_message_text_supprime_bloc_kickstarter():
    text = "Intro\n\nKickstarter: lien supprimé\n\nConclusion"
    result = discord_bot.clean_message_text(text, "Alice", _TS)
    assert "Kickstarter" not in result
    assert "Conclusion" in result


def test_clean_message_text_supprime_bloc_a_underscore():
    text = "Texte\n\n>A_store.steampowered.com\nTitre jeu\n\nSuite"
    result = discord_bot.clean_message_text(text, "Alice", _TS)
    assert ">A_" not in result
    assert "Suite" in result


def test_clean_message_text_supprime_citation():
    text = "Bob - MODERATOR\nOP\nReste du message"
    result = discord_bot.clean_message_text(text, "Alice", _TS)
    assert "Bob - MODERATOR" not in result
    assert "Reste du message" in result


def test_clean_message_text_lignes_vides_multiples():
    text = "Ligne 1\n\n\n\nLigne 2"
    result = discord_bot.clean_message_text(text, "Alice", _TS)
    assert "\n\n\n" not in result
    assert "Ligne 1" in result
    assert "Ligne 2" in result


def test_clean_message_text_timestamp_invalide():
    result = discord_bot.clean_message_text("Texte", "Alice", "pas-une-date")
    assert result.startswith("[pas-une-date - Alice]")


def test_clean_message_text_tout_vide_apres_nettoyage():
    # Un message ne contenant qu'une URL doit toujours retourner le préfixe.
    result = discord_bot.clean_message_text("https://example.com", "Alice", _TS)
    assert result == _PREFIX
