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
    # https://a (premier https inconnu) → site_officiel ; https://b → liens
    assert payload["website_studio"] == "https://a"
    assert payload["liens"] == "https://b"
    assert payload["pieces_jointes"] == ["https://p"]


def test_build_lead_payload_listes_multiples():
    yt1 = "https://www.youtube.com/watch?v=aaa"
    yt2 = "https://youtu.be/bbb"
    tw1 = "https://twitter.com/u/1"
    tw2 = "https://x.com/u/2"
    gdoc1 = "https://docs.google.com/presentation/d/abc"
    gdoc2 = "https://docs.google.com/document/d/xyz"
    pitch1 = "https://pitch.com/deck1"
    pitch2 = "https://exemple.com/deck2.pdf"
    payload = discord_bot.build_lead_payload(
        "Jeu",
        ["m"],
        [yt1, yt2, tw1, tw2, gdoc1, gdoc2, pitch1, pitch2],
        [],
        "2026-06-01",
        raw_text=f"exec {gdoc1} exec {gdoc2}",
    )
    assert payload["youtubes"] == [yt1, yt2]
    assert payload["twitters"] == [tw1, tw2]
    assert payload["exec_docs"] == [gdoc1, gdoc2]
    assert payload["pitch_decks"] == [pitch1, pitch2]
    assert "youtube" not in payload
    assert "twitter" not in payload
    assert "exec_doc" not in payload
    assert "pitch_deck" not in payload


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

def _fake_embed(title=None, description=None):
    return SimpleNamespace(title=title, description=description)


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
        embeds=[],
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
    assert record["embed_text"] == ""
    assert record["game"] is None


def test_build_message_record_sans_steam():
    msg = _fake_message()
    msg.content = "Juste un commentaire sans lien"
    msg.attachments = []
    record = discord_bot.build_message_record(msg)
    assert record["links"] == []
    assert record["steam_links"] == []
    assert record["attachments"] == []


def test_build_message_record_embed_title_et_description():
    msg = _fake_message()
    msg.embeds = [
        _fake_embed(title="Exec Doc — Tabula", description="Résumé exécutif du projet"),
        _fake_embed(title="Fiche Steam", description=None),
    ]
    record = discord_bot.build_message_record(msg)
    assert "Exec Doc — Tabula" in record["embed_text"]
    assert "Résumé exécutif du projet" in record["embed_text"]
    assert "Fiche Steam" in record["embed_text"]


def test_build_message_record_embed_sans_attributs():
    # Embed sans title ni description → embed_text vide.
    msg = _fake_message()
    msg.embeds = [_fake_embed(title=None, description=None)]
    record = discord_bot.build_message_record(msg)
    assert record["embed_text"] == ""


def test_build_message_record_embed_alimente_exec_context():
    # Quand "exec" est dans le titre d'un embed, _is_exec_context doit le détecter
    # via le raw_text combiné (content + embed_text).
    gdoc = "https://docs.google.com/presentation/d/abc"
    msg = _fake_message()
    msg.content = gdoc
    msg.embeds = [_fake_embed(title="Exec summary Tabula", description=None)]
    record = discord_bot.build_message_record(msg)
    raw = f"{record['text']}\n{record['embed_text']}".strip()
    assert discord_bot._is_exec_context(gdoc, raw) is True


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


# --- _is_exec_context --------------------------------------------------------

_GDOC = "https://docs.google.com/presentation/d/abc123"


def test_is_exec_context_exec_avant():
    raw = f"voici notre exec deck {_GDOC} à consulter"
    assert discord_bot._is_exec_context(_GDOC, raw) is True


def test_is_exec_context_exec_apres():
    raw = f"{_GDOC} — résumé exec à partager"
    assert discord_bot._is_exec_context(_GDOC, raw) is True


def test_is_exec_context_exec_absent():
    raw = f"notre pitch deck {_GDOC} pour les investisseurs"
    assert discord_bot._is_exec_context(_GDOC, raw) is False


def test_is_exec_context_exec_hors_fenetre():
    # "exec" est à plus de 150 chars après le lien → non détecté.
    padding = "x" * 151
    raw = f"{_GDOC} {padding} exec"
    assert discord_bot._is_exec_context(_GDOC, raw) is False


def test_is_exec_context_url_absente():
    assert discord_bot._is_exec_context(_GDOC, "aucun lien ici") is False


# --- _sort_liens -------------------------------------------------------------

def _sort(liens, raw_text=""):
    """Raccourci de test pour dépaquetter _sort_liens en dict nommé."""
    steam, ks, pitch_decks, exec_docs, youtubes, twitters, site, autres_steam, autres = discord_bot._sort_liens(liens, raw_text)
    return {
        "steam": steam, "kickstarter": ks,
        "pitch_decks": pitch_decks, "exec_docs": exec_docs,
        "youtubes": youtubes, "twitters": twitters,
        "website_studio": site, "autres_steam": autres_steam, "autres": autres,
    }


def test_sort_liens_gdoc_avec_exec_va_dans_exec_docs():
    raw = f"notre exec summary {_GDOC}"
    r = _sort([_GDOC], raw)
    assert r["exec_docs"] == [_GDOC]
    assert r["pitch_decks"] == []


def test_sort_liens_gdoc_sans_exec_va_dans_pitch_decks():
    raw = f"notre pitch deck {_GDOC}"
    r = _sort([_GDOC], raw)
    assert r["pitch_decks"] == [_GDOC]
    assert r["exec_docs"] == []


def test_sort_liens_gdoc_sans_raw_text_va_dans_pitch_decks():
    r = _sort([_GDOC])
    assert r["pitch_decks"] == [_GDOC]
    assert r["exec_docs"] == []


def test_sort_liens_pdf_et_pitch_non_affectes():
    liens = ["https://exemple.com/deck.pdf", "https://pitch.com/mon-pitch"]
    r = _sort(liens, "exec exec exec")
    assert r["pitch_decks"] == ["https://exemple.com/deck.pdf", "https://pitch.com/mon-pitch"]
    assert r["exec_docs"] == []


def test_sort_liens_steam_kickstarter_non_affectes():
    steam_url = "https://store.steampowered.com/app/1/"
    ks_url = "https://www.kickstarter.com/projects/x"
    r = _sort([steam_url, ks_url, _GDOC], f"exec {_GDOC}")
    assert r["steam"] == steam_url
    assert r["kickstarter"] == ks_url
    assert r["exec_docs"] == [_GDOC]
    assert r["pitch_decks"] == []


def test_sort_liens_exec_doc_dedup_liste_complete():
    gdoc2 = "https://docs.google.com/document/d/xyz"
    r = _sort([_GDOC, gdoc2], f"exec {_GDOC} exec {gdoc2}")
    assert r["exec_docs"] == [_GDOC, gdoc2]
    assert r["pitch_decks"] == []
    assert r["autres"] == []


# --- _sort_liens : youtube, twitter, site_officiel ---------------------------

def test_sort_liens_youtube_com():
    r = _sort(["https://www.youtube.com/watch?v=abc123"])
    assert r["youtubes"] == ["https://www.youtube.com/watch?v=abc123"]


def test_sort_liens_youtu_be():
    r = _sort(["https://youtu.be/abc123"])
    assert r["youtubes"] == ["https://youtu.be/abc123"]


def test_sort_liens_youtube_dedup_liste_complete():
    yt1 = "https://www.youtube.com/watch?v=aaa"
    yt2 = "https://youtu.be/bbb"
    r = _sort([yt1, yt2])
    assert r["youtubes"] == [yt1, yt2]
    assert r["autres"] == []


def test_sort_liens_twitter_com():
    r = _sort(["https://twitter.com/user/status/123"])
    assert r["twitters"] == ["https://twitter.com/user/status/123"]


def test_sort_liens_x_com():
    r = _sort(["https://x.com/user/status/456"])
    assert r["twitters"] == ["https://x.com/user/status/456"]


def test_sort_liens_twitter_dedup_liste_complete():
    tw1 = "https://twitter.com/user/status/111"
    tw2 = "https://x.com/user/status/222"
    r = _sort([tw1, tw2])
    assert r["twitters"] == [tw1, tw2]
    assert r["autres"] == []


def test_sort_liens_site_officiel_https_inconnu():
    r = _sort(["https://mon-studio.fr"])
    assert r["website_studio"] == "https://mon-studio.fr"
    assert r["autres"] == []


def test_sort_liens_site_officiel_dedup_second_dans_autres():
    r = _sort(["https://studio-a.com", "https://studio-b.com"])
    assert r["website_studio"] == "https://studio-a.com"
    assert r["autres"] == ["https://studio-b.com"]


def test_sort_liens_site_officiel_http_non_capture():
    # URL http:// (sans s) → autres, pas site_officiel.
    r = _sort(["http://vieux-site.com"])
    assert r["website_studio"] is None
    assert r["autres"] == ["http://vieux-site.com"]


def test_sort_liens_toutes_categories_ensemble():
    liens = [
        "https://store.steampowered.com/app/1/",
        "https://www.kickstarter.com/projects/x",
        "https://youtu.be/abc",
        "https://x.com/studio",
        "https://mon-studio.fr",
        _GDOC,
    ]
    r = _sort(liens)
    assert r["steam"] == "https://store.steampowered.com/app/1/"
    assert r["kickstarter"] == "https://www.kickstarter.com/projects/x"
    assert r["youtubes"] == ["https://youtu.be/abc"]
    assert r["twitters"] == ["https://x.com/studio"]
    assert r["website_studio"] == "https://mon-studio.fr"
    assert r["pitch_decks"] == [_GDOC]
    assert r["autres"] == []
