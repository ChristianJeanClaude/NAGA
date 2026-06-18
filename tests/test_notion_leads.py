"""Tests des fonctions pures de notion_leads (aucun appel réseau).

Conformément aux contraintes du projet, ces tests n'effectuent aucun appel HTTP :
on ne teste que la construction des propriétés Notion (_build_properties) et la
troncature des champs rich_text à la limite Notion de 2000 caractères.
"""

import notion_leads


# --- _rich_text --------------------------------------------------------------

def test_rich_text_tronque_a_2000():
    prop = notion_leads._rich_text("a" * 5000)
    contenu = prop["rich_text"][0]["text"]["content"]
    assert len(contenu) == notion_leads.NOTION_RICH_TEXT_LIMIT == 2000


def test_rich_text_court_inchange():
    prop = notion_leads._rich_text("bonjour")
    assert prop["rich_text"][0]["text"]["content"] == "bonjour"


def test_rich_text_limite_exacte_inchangee():
    texte = "x" * 2000
    prop = notion_leads._rich_text(texte)
    assert prop["rich_text"][0]["text"]["content"] == texte


def _utf16_units(s):
    return len(s.encode("utf-16-le")) // 2


def test_truncate_utf16_emoji_ne_depasse_pas_2000_unites():
    # 1998 ASCII + 2 emoji hors BMP = 2000 points de code mais 2002 unités UTF-16.
    s = "a" * 1998 + "🧱🧱"
    assert len(s) == 2000 and _utf16_units(s) == 2002
    tronque = notion_leads._truncate_utf16(s, 2000)
    assert _utf16_units(tronque) <= 2000


def test_truncate_utf16_demi_paire_substitution_jetee():
    # Coupe au milieu d'une paire de substitution → la demi-paire est jetée.
    s = "a" * 1999 + "🧱"
    tronque = notion_leads._truncate_utf16(s, 2000)
    assert tronque == "a" * 1999
    assert _utf16_units(tronque) <= 2000


def test_truncate_utf16_court_inchange():
    assert notion_leads._truncate_utf16("bonjour 🧱", 2000) == "bonjour 🧱"


def test_rich_text_emoji_respecte_limite_notion():
    # Le helper utilisé par _build_properties doit produire ≤ 2000 unités UTF-16.
    s = "é" * 1000 + "🧱" * 600  # 1000 BMP + 600 hors BMP = 2200 unités UTF-16
    contenu = notion_leads._rich_text(s)["rich_text"][0]["text"]["content"]
    assert _utf16_units(contenu) <= 2000


def test_build_properties_messages_emoji_respecte_limite_notion():
    # Cas réel BOTANICA : messages avec emoji, 2000 points de code mais > 2000 UTF-16.
    messages = "a" * 1998 + "🧱🧱"
    props = notion_leads._build_properties({"nom_du_jeu": "X", "messages": messages})
    contenu = props["Messages"]["rich_text"][0]["text"]["content"]
    assert _utf16_units(contenu) <= 2000


# --- _build_properties : troncature des champs rich_text ----------------------

def test_build_properties_messages_tronque():
    props = notion_leads._build_properties({"nom_du_jeu": "X", "messages": "m" * 5000})
    assert len(props["Messages"]["rich_text"][0]["text"]["content"]) == 2000


def test_build_properties_liens_tronque():
    props = notion_leads._build_properties({"nom_du_jeu": "X", "liens": "l" * 5000})
    assert len(props["Liens"]["rich_text"][0]["text"]["content"]) == 2000


def test_build_properties_description_tronque():
    props = notion_leads._build_properties({"nom_du_jeu": "X", "description_jeu": "d" * 5000})
    assert len(props["Description jeux"]["rich_text"][0]["text"]["content"]) == 2000


def test_build_properties_champs_courts_intacts():
    props = notion_leads._build_properties({
        "nom_du_jeu": "Hollow Knight",
        "messages": "court",
        "studio": "Team Cherry",
    })
    assert props["Nom du jeu"]["title"][0]["text"]["content"] == "Hollow Knight"
    assert props["Messages"]["rich_text"][0]["text"]["content"] == "court"
    assert props["Studio"]["rich_text"][0]["text"]["content"] == "Team Cherry"


def test_build_properties_fathom_mappe_vers_enregistrement_fathom():
    props = notion_leads._build_properties({
        "nom_du_jeu": "X",
        "fathoms": ["https://fathom.video/calls/1", "https://fathom.video/share/2"],
    })
    contenu = props["Enregistrement fathom"]["rich_text"][0]["text"]["content"]
    assert contenu == "https://fathom.video/calls/1\nhttps://fathom.video/share/2"


def test_build_properties_fathom_tronque():
    props = notion_leads._build_properties({"nom_du_jeu": "X", "fathoms": ["f" * 5000]})
    assert len(props["Enregistrement fathom"]["rich_text"][0]["text"]["content"]) == 2000


def test_enregistrement_fathom_dans_rich_text_columns():
    assert "Enregistrement fathom" in notion_leads.RICH_TEXT_COLUMNS


def test_build_properties_drive_mappe_vers_drive_assets():
    props = notion_leads._build_properties({
        "nom_du_jeu": "X",
        "drives": ["https://drive.google.com/file/d/1", "https://1drv.ms/f/2"],
    })
    contenu = props["Drive / Assets"]["rich_text"][0]["text"]["content"]
    assert contenu == "https://drive.google.com/file/d/1\nhttps://1drv.ms/f/2"


def test_build_properties_drive_tronque():
    props = notion_leads._build_properties({"nom_du_jeu": "X", "drives": ["d" * 5000]})
    assert len(props["Drive / Assets"]["rich_text"][0]["text"]["content"]) == 2000


def test_drive_assets_dans_rich_text_columns():
    assert "Drive / Assets" in notion_leads.RICH_TEXT_COLUMNS


def test_build_properties_instagram_mappe_vers_instagram():
    props = notion_leads._build_properties({
        "nom_du_jeu": "X",
        "instagrams": ["https://instagram.com/a", "https://instagram.com/b"],
    })
    contenu = props["Instagram"]["rich_text"][0]["text"]["content"]
    assert contenu == "https://instagram.com/a\nhttps://instagram.com/b"


def test_build_properties_canva_mappe_vers_canva():
    props = notion_leads._build_properties({
        "nom_du_jeu": "X",
        "canvas": ["https://canva.link/a", "https://www.canva.com/design/b"],
    })
    contenu = props["Canva"]["rich_text"][0]["text"]["content"]
    assert contenu == "https://canva.link/a\nhttps://www.canva.com/design/b"


def test_build_properties_instagram_canva_tronques():
    props = notion_leads._build_properties({
        "nom_du_jeu": "X", "instagrams": ["i" * 5000], "canvas": ["c" * 5000],
    })
    assert len(props["Instagram"]["rich_text"][0]["text"]["content"]) == 2000
    assert len(props["Canva"]["rich_text"][0]["text"]["content"]) == 2000


def test_instagram_canva_dans_rich_text_columns():
    assert "Instagram" in notion_leads.RICH_TEXT_COLUMNS
    assert "Canva" in notion_leads.RICH_TEXT_COLUMNS


def test_build_properties_omet_les_champs_absents():
    props = notion_leads._build_properties({"nom_du_jeu": "X"})
    assert "Messages" not in props
    assert "Liens" not in props
    assert props["Nom du jeu"]["title"][0]["text"]["content"] == "X"
