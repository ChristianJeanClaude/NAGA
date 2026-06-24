# NAGA — Bot Discord → Notion (leads jeux)

Bot Discord **lecture seule** qui scrape un channel forum et pousse les
conversations dans Notion, pour le suivi des leads de jeux indé.

- **Base Leads / CRM** (`bots/notion_leads.py`) — une fiche par thread de forum :
  conversation agrégée, liens triés par catégorie, métadonnées Steam. La
  conversation **intégrale** est écrite dans le **corps de la page** ; la propriété
  `Messages` n'en garde qu'un aperçu (messages récents) pour la vue tableau.
- **Base archive** (optionnelle, dans `bots/discord_bot.py`) — une page par thread
  avec **tous les messages** recopiés bloc par bloc. Désactivable via
  `ARCHIVE_ENABLED=false` (recommandé si seul le CRM est utilisé : scrape bien
  plus rapide).

Au démarrage le bot parcourt tout l'historique du channel (threads actifs +
archivés), puis écoute les nouveaux messages en temps réel. L'idempotence repose
sur la propriété Notion **`Thread ID`** : pas de doublon au redémarrage.

## Structure du projet

```
.
├── bots/
│   ├── discord_bot.py        # Bot principal : scraping, parsing, tri des liens, archive
│   └── notion_leads.py       # Client Notion de la base Leads/CRM (upsert + corps de page)
├── tools/                    # Diagnostics / migrations ponctuels (lecture seule sauf mention)
│   ├── list_forum_threads.py     # liste les threads du forum
│   ├── inspect_leads_db.py       # affiche le schéma de la base Leads
│   ├── list_databases.py         # liste les bases accessibles par le token
│   ├── compare_bases.py          # compare base archive ↔ base Leads
│   ├── list_website_studio.py    # repère les liens « Website studio » douteux
│   ├── reconcile_crm.py          # associe threads Discord ↔ lignes manuelles du CRM (--apply)
│   └── migrate_notion_format.py  # migration d'idempotence (ancien, ponctuel)
├── tests/
│   ├── test_discord_bot.py   # tests des fonctions pures de discord_bot
│   └── test_notion_leads.py  # tests des fonctions pures de notion_leads
├── conftest.py               # rend bots/ importable par les tests
├── Procfile                  # worker: python bots/discord_bot.py (Railway)
├── requirements.txt          # discord.py, pytest
├── CLAUDE.md                 # conventions & contraintes du projet
└── README.md
```

## Flux de données (résumé)

```
Discord (forum)
  └─ on_ready → all_forum_threads → _scrape_thread
       └─ process_message → build_message_record → resolve_game (API Steam)
            └─ _accumulate_lead (mémoire, par thread)
  fin de thread → _push_thread_lead
       └─ build_lead_payload (_sort_liens) → notion_leads.push_to_notion
            ├─ propriétés (titre, Lien Steam, Messages aperçu, liens triés, Tags…)
            └─ corps de page : 1 bloc paragraphe par message (gate « Conv sync »)
  temps réel : on_message / on_thread_create / on_thread_update
```

Points clés :

- **Tri des liens** (`_sort_liens`) : Steam, Kickstarter, Pitch Deck, Exec Doc,
  YouTube, Twitter/X, Fathom, Drive/Assets, Instagram, Canva, site studio.
- **Adaptation au schéma** : `ensure_schema()` **détecte la propriété titre** de la
  base cible (ex. `Jeu`), crée les colonnes gérées manquantes (dont `Thread ID`,
  `Conv sync`) et corrige les types divergents — **sans jamais toucher** aux champs
  humains du CRM (Statut, Priorité, Owner, Next step, Notes…).
- **Corps de page** : réécrit uniquement si la conversation change (empreinte
  `Conv sync`, versionnée par format). Le contenu humain placé **au-dessus** du
  marqueur « 💬 Conversation Discord » est préservé.

## Configuration

Secrets jamais hardcodés : lus depuis `~/.env.*` en local, ou depuis les variables
d'environnement (Railway, Docker…).

| Fichier / env     | Clés                                                                   |
|-------------------|------------------------------------------------------------------------|
| `~/.env.discord`  | `DISCORD_TOKEN`, `DISCORD_CHANNEL_ID`                                   |
| `~/.env.notion`   | `NOTION_TOKEN`, `NOTION_DB_LEADS_ID`, `NOTION_PARENT_PAGE_ID`*, `NOTION_TOKEN_LEADS`* |

Variables optionnelles :

- **`ARCHIVE_ENABLED`** (défaut `true`) : à `false`, la base archive est désactivée
  et `NOTION_PARENT_PAGE_ID` n'est plus requis (le bot ne pousse que vers le CRM).
- **`NOTION_DB_LEADS_ID`** : id de la base Leads/CRM ; à défaut, l'id codé en dur
  dans `notion_leads.py`.
- **`NOTION_TOKEN_LEADS`** : token dédié à la base Leads ; à défaut, repli sur
  `NOTION_TOKEN`.
- **`NOTION_PARENT_PAGE_ID`** : requis seulement si l'archive est active — page
  parente (le bot y crée « NAGA — Jeux Discord ») ou database existante à adopter.

Prérequis : intent Discord **MESSAGE CONTENT** activé, et intégration Notion
**partagée** avec la/les base(s) cible(s) (sinon 401/404).

## Lancement

```bash
pip install -r requirements.txt

# Bot (CRM seul, scrape rapide)
ARCHIVE_ENABLED=false python bots/discord_bot.py     # Windows cmd : set ARCHIVE_ENABLED=false

# Diagnostics (depuis la racine)
python tools/inspect_leads_db.py
python tools/list_forum_threads.py
python tools/compare_bases.py

# Réconciliation CRM (associe threads ↔ lignes manuelles ; --apply pour écrire)
python tools/reconcile_crm.py
python tools/reconcile_crm.py --apply
```

Déploiement : `Procfile` (`worker: python bots/discord_bot.py`). Une **seule
instance** doit tourner par token Discord (une seule connexion gateway autorisée).

## Tests

```bash
python -m pytest
```

`conftest.py` ajoute `bots/` au `sys.path`. Les tests n'effectuent **aucun appel
réseau** (contrainte projet) : seules les fonctions pures sont testées.
