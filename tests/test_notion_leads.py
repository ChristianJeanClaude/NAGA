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


# --- adaptation au CRM « Pipeline Prospects Jeux » ---------------------------

def test_build_properties_titre_par_defaut_nom_du_jeu():
    props = notion_leads._build_properties({"nom_du_jeu": "X"})
    assert props["Nom du jeu"]["title"][0]["text"]["content"] == "X"


def test_build_properties_titre_configurable():
    # Le bot doit pouvoir écrire dans une base dont le titre est « Jeu » (CRM).
    props = notion_leads._build_properties({"nom_du_jeu": "Chess Nuke"}, title_prop="Jeu")
    assert props["Jeu"]["title"][0]["text"]["content"] == "Chess Nuke"
    assert "Nom du jeu" not in props


def test_build_properties_steam_mappe_vers_lien_steam():
    props = notion_leads._build_properties({"nom_du_jeu": "X", "steam_url": "https://store.steampowered.com/app/1/"})
    assert props["Lien Steam"]["url"] == "https://store.steampowered.com/app/1/"
    assert "Steam URL" not in props


def test_build_properties_date_mappe_vers_dernier_echange():
    props = notion_leads._build_properties({"nom_du_jeu": "X", "date": "2026-06-18"})
    assert props["Dernier échange"]["date"]["start"] == "2026-06-18"
    assert "Dernier message" not in props


def test_managed_columns_contient_les_cles_du_crm():
    # Colonnes techniques/scrapées que le bot doit gérer, sans les champs humains.
    for col in ("Thread ID", "Lien Steam", "Dernier échange", "Messages",
                "YouTube", "Enregistrement fathom", "Drive / Assets", "Instagram", "Canva"):
        assert col in notion_leads.MANAGED_COLUMNS
    # Le titre n'est pas une colonne gérée (créée/typée), et les champs humains non plus.
    for humain in ("Statut", "Priorité", "Type de deal", "Owner NAGA", "Next step", "Notes", "Insights"):
        assert humain not in notion_leads.MANAGED_COLUMNS


def test_dernier_echange_dans_always_update():
    # La date du dernier message doit être rafraîchie à chaque push.
    assert "Dernier échange" in notion_leads.ALWAYS_UPDATE


# --- corps de page : conversation intégrale (blocs) --------------------------

def _para_texts(blocks):
    return [b["paragraph"]["rich_text"][0]["text"]["content"] for b in blocks if b["type"] == "paragraph"]


def test_chunk_text_respecte_limite_utf16():
    chunks = notion_leads._chunk_text("a" * 5000, 2000)
    assert all(_utf16_units(c) <= 2000 for c in chunks)
    assert "".join(chunks) == "a" * 5000


def test_chunk_text_emoji_respecte_limite():
    chunks = notion_leads._chunk_text("🧱" * 1500, 2000)  # 3000 unités UTF-16
    assert all(_utf16_units(c) <= 2000 for c in chunks)
    assert "".join(chunks) == "🧱" * 1500


def test_conversation_blocks_marqueur_en_tete():
    blocks = notion_leads._conversation_blocks(["salut"])
    assert blocks[0]["type"] == "heading_2"
    assert blocks[0]["heading_2"]["rich_text"][0]["text"]["content"] == notion_leads.CONV_MARKER


def test_conversation_blocks_un_bloc_par_message():
    blocks = notion_leads._conversation_blocks(["msg A", "msg B", "msg C"])
    paras = _para_texts(blocks)
    assert paras == ["msg A", "msg B", "msg C"]  # un paragraphe par message


def test_conversation_blocks_message_long_redecoupe():
    # Un message > 2000 unités UTF-16 s'étale sur plusieurs blocs, sans perte.
    blocks = notion_leads._conversation_blocks(["x" * 5000])
    paras = _para_texts(blocks)
    assert len(paras) >= 2 and all(_utf16_units(p) <= 2000 for p in paras)
    assert "".join(paras) == "x" * 5000


def test_conversation_blocks_message_avec_lignes_internes_reste_un_bloc():
    # Un message contenant une ligne vide interne ne doit PAS être scindé.
    blocks = notion_leads._conversation_blocks(["Titre\n\nDétail sur deux paragraphes"])
    paras = _para_texts(blocks)
    assert paras == ["Titre\n\nDétail sur deux paragraphes"]


def test_conversation_blocks_ignore_messages_vides():
    blocks = notion_leads._conversation_blocks(["", "   ", "vrai message"])
    assert _para_texts(blocks) == ["vrai message"]


def test_conversation_blocks_vide():
    blocks = notion_leads._conversation_blocks([])
    assert len(blocks) == 1 and blocks[0]["type"] == "heading_2"  # juste le marqueur


def test_conv_sig_deterministe_et_sensible():
    assert notion_leads._conv_sig("abc") == notion_leads._conv_sig("abc")
    assert notion_leads._conv_sig("abc") != notion_leads._conv_sig("abd")


def test_conv_sync_dans_managed_columns():
    assert "Conv sync" in notion_leads.MANAGED_COLUMNS


# --- _update_patch : ce qui est réécrit sur une page existante ---------------

def _email_prop(address):
    return {"type": "email", "email": address}


def test_email_absent_de_always_update():
    assert "Email" not in notion_leads.ALWAYS_UPDATE


def test_update_patch_email_existant_non_ecrase():
    properties = notion_leads._build_properties({"nom_du_jeu": "J", "email": "bot@studio.io"})
    patch = notion_leads._update_patch(properties, {"Email": _email_prop("humain@studio.io")})
    assert "Email" not in patch


def test_update_patch_email_vide_rempli():
    properties = notion_leads._build_properties({"nom_du_jeu": "J", "email": "bot@studio.io"})
    patch = notion_leads._update_patch(properties, {"Email": _email_prop(None)})
    assert patch["Email"] == {"email": "bot@studio.io"}


def test_update_patch_always_update_ecrase():
    properties = notion_leads._build_properties({"nom_du_jeu": "J", "messages": "nouveau"})
    existing = {"Messages": {"type": "rich_text", "rich_text": [{"plain_text": "ancien"}]}}
    assert "Messages" in notion_leads._update_patch(properties, existing)


# --- adoption des fiches créées à la main ------------------------------------

def _page(page_id, title, title_prop="Jeu"):
    return {"id": page_id, "properties": {
        title_prop: {"type": "title", "title": [{"plain_text": title}]},
        "Thread ID": {"type": "rich_text", "rich_text": []},
    }}


def test_normalize_title_casse_et_espaces():
    assert notion_leads._normalize_title("Battle Only (Tatakau Dake)") == \
        notion_leads._normalize_title("  battle  only(tatakau dake) ")
    assert notion_leads._normalize_title(None) == ""


def test_page_title_quel_que_soit_le_nom_de_la_propriete():
    assert notion_leads._page_title(_page("p", "Absym", title_prop="Nom du jeu")) == "Absym"
    assert notion_leads._page_title({"properties": {}}) == ""


def test_pick_adoptable_fiche_manuelle_unique():
    pages = [_page("manuelle", "Battle Only (Tatakau Dake)"), _page("autre", "Absym")]
    assert notion_leads._pick_adoptable(pages, "battle only (tatakau dake)") == "manuelle"


def test_pick_adoptable_homonymes_pas_d_adoption():
    pages = [_page("a", "Absym"), _page("b", "ABSYM ")]
    assert notion_leads._pick_adoptable(pages, "Absym") is None


def test_pick_adoptable_aucune_correspondance():
    assert notion_leads._pick_adoptable([_page("a", "Absym")], "Kiryon") is None
    assert notion_leads._pick_adoptable([], "Kiryon") is None


def test_update_patch_adoption_ne_remplit_que_les_vides():
    properties = notion_leads._build_properties(
        {"nom_du_jeu": "Battle Only (Tatakau Dake)", "thread_id": "1571", "messages": "fil",
         "tags": ["Prio"], "studio": "Studio X"},
        "Jeu",
    )
    existing = {
        "Jeu": {"type": "title", "title": [{"plain_text": "Battle Only (Tatakau Dake)"}]},
        "Thread ID": {"type": "rich_text", "rich_text": []},
        "Messages": {"type": "rich_text", "rich_text": [{"plain_text": "note humaine"}]},
        "Tags": {"type": "multi_select", "multi_select": [{"name": "CoPublish"}]},
        "Studio": {"type": "rich_text", "rich_text": []},
    }
    patch = notion_leads._update_patch(properties, existing, only_empty=True)
    assert set(patch) == {"Thread ID", "Studio"}  # titre, Messages, Tags humains intacts
    assert patch["Thread ID"]["rich_text"][0]["text"]["content"] == "1571"



# --- pièces jointes : téléversement et repli ---------------------------------

_CDN = "https://cdn.discordapp.com/attachments/1/2/deck%20final.pdf?ex=6b01&is=6b00&hm=af01"
_JUMP = "https://discord.com/channels/1/2/3"


def test_is_discord_cdn():
    assert notion_leads._is_discord_cdn(_CDN)
    assert notion_leads._is_discord_cdn("https://media.discordapp.net/attachments/1/2/a.png")
    assert not notion_leads._is_discord_cdn(_JUMP)
    assert not notion_leads._is_discord_cdn("https://cdn.discordapp.com.evil.io/a.png")
    assert not notion_leads._is_discord_cdn(None)


def test_filename_from_url_sans_signature():
    assert notion_leads._filename_from_url(_CDN) == "deck final.pdf"


def test_fallback_entry_pointe_le_message_discord():
    entry = notion_leads._fallback_entry({"url": _CDN, "filename": "deck.pdf", "jump_url": _JUMP})
    assert entry == {"name": "deck.pdf", "external": {"url": _JUMP}}


def test_fallback_entry_sans_lien_du_message_garde_l_url():
    entry = notion_leads._fallback_entry({"url": _CDN})
    assert entry == {"name": "deck final.pdf", "external": {"url": _CDN}}


def test_build_properties_pieces_jointes_nom_sans_query():
    props = notion_leads._build_properties({"nom_du_jeu": "J", "pieces_jointes": [_CDN]})
    assert props["Pièces jointes"]["files"][0]["name"] == "deck final.pdf"


def test_build_properties_pieces_jointes_limite_notion():
    pieces = [f"https://cdn.discordapp.com/attachments/1/{i}/a.png" for i in range(150)]
    props = notion_leads._build_properties({"nom_du_jeu": "J", "pieces_jointes": pieces})
    assert len(props["Pièces jointes"]["files"]) == notion_leads.NOTION_FILES_LIMIT


def _files_prop(*files):
    return {"type": "files", "files": list(files)}


def _external(url):
    return {"name": "x", "type": "external", "external": {"url": url}}


def test_attachments_stale_liens_cdn_seulement():
    assert notion_leads._attachments_stale(_files_prop(_external(_CDN), _external(_CDN)))


def test_attachments_stale_faux_si_fichier_televerse_ou_lien_message():
    uploaded = {"name": "deck.pdf", "type": "file", "file": {"url": "https://prod-files.notion/x"}}
    assert not notion_leads._attachments_stale(_files_prop(_external(_CDN), uploaded))
    assert not notion_leads._attachments_stale(_files_prop(_external(_JUMP)))
    assert not notion_leads._attachments_stale(_files_prop())


def test_attachments_writable():
    assert notion_leads._attachments_writable(_files_prop())                    # vide
    assert notion_leads._attachments_writable(_files_prop(_external(_CDN)))     # liens morts
    uploaded = {"name": "a", "type": "file", "file": {"url": "https://prod-files.notion/x"}}
    assert not notion_leads._attachments_writable(_files_prop(uploaded))        # déjà téléversé


def test_multipart_body_champ_file():
    body = notion_leads._multipart_body('deck "v2".pdf', "application/pdf", b"%PDF-1.4", "XYZ")
    assert body.startswith(b"--XYZ\r\n")
    assert b'Content-Disposition: form-data; name="file"; filename="deck \'v2\'.pdf"' in body
    assert b"Content-Type: application/pdf\r\n\r\n%PDF-1.4\r\n--XYZ--\r\n" in body


def test_download_refuse_un_hote_hors_cdn_discord():
    # Vérifié avant toute requête : aucun appel réseau.
    assert notion_leads._download_discord_attachment("https://example.com/a.pdf", 10) is None


def test_upload_attachment_trop_lourd_sans_telechargement():
    piece = {"url": _CDN, "size": notion_leads.MAX_UPLOAD_BYTES + 1}
    assert notion_leads._upload_attachment(piece) is None
