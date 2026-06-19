# CLAUDE.md

## Projet

Bot Discord lecture seule qui scrape un channel forum et pousse les données dans deux bases Notion :

- **NAGA — Jeux Discord** — une page par thread de forum, avec tous les messages du thread, les liens Steam détectés et les pièces jointes.
- **Leads Discord** — une page par thread agrégée (texte nettoyé, liens triés par catégorie, métadonnées Steam), destinée au suivi des leads jeux indé.

Au démarrage le bot parcourt tout l'historique du channel (threads actifs + archivés), puis écoute les nouveaux messages en temps réel. L'idempotence repose sur la propriété Notion « Message IDs » : pas de doublon au redémarrage.

## Fichiers principaux

```
bots/
  discord_bot.py      — bot principal : scraping, parsing, upsert Notion
  notion_leads.py     — client Notion pour la base Leads (module indépendant)
tools/
  list_forum_threads.py     — diagnostic : liste les threads du forum cible
  inspect_leads_db.py       — diagnostic : affiche le schéma de la base Leads (lecture seule)
  migrate_notion_format.py  — migration ponctuelle : marqueurs → propriété Message IDs
tests/
  test_discord_bot.py — tests des fonctions pures (aucun appel réseau)
conftest.py           — ajoute bots/ au sys.path pour les imports de test
```

## Stack technique

- **Python 3.7+** — `sys.stdout.reconfigure` requiert 3.7 ; walrus operator (`:=`) requiert 3.8+
- **`discord.py`** — client Discord (à installer : `pip install discord.py`)
- **`urllib`** — appels HTTP Notion et Steam API (stdlib, pas de `requests`)
- **`asyncio`** — boucle d'événements Discord ; I/O urllib exécuté via `asyncio.to_thread`
- **`re`, `json`, `pathlib`, `collections`** — stdlib uniquement dans les scripts principaux
- **`pytest`** — framework de test (à installer : `pip install pytest`)

## Secrets

Deux fichiers `KEY=VALUE` dans `~` (jamais hardcodés, jamais committés) :

- `~/.env.discord` — `DISCORD_TOKEN`, `DISCORD_CHANNEL_ID`
- `~/.env.notion` — `NOTION_TOKEN`, `NOTION_PARENT_PAGE_ID`, `NOTION_TOKEN_LEADS` (optionnel), `NOTION_DB_LEADS_ID` (optionnel)

Si `NOTION_TOKEN_LEADS` est absent, le bot retombe sur `NOTION_TOKEN` pour la base Leads (même intégration). Le push n'est désactivé que si aucun token Notion n'est disponible.

`NOTION_DB_LEADS_ID` permet de pointer la base Leads sans toucher au code ; à défaut, l'id par défaut codé en dur dans `bots/notion_leads.py` est utilisé.

Variable d'environnement optionnelle `ARCHIVE_ENABLED` (défaut : `true`) : à `false`, la base archive « NAGA — Jeux Discord » est désactivée — `NOTION_PARENT_PAGE_ID` n'est plus requis et le bot ne pousse que vers la base Leads (scrape bien plus rapide). `notion_leads.ensure_schema()` détecte automatiquement la propriété titre de la base cible (ex. `Jeu`) et crée ses colonnes gérées sans toucher aux champs humains.

## Commandes de lancement

```bash
# Lancer le bot (depuis la racine du projet)
py bots/discord_bot.py

# Lister les threads du forum (diagnostic, lecture seule)
py tools/list_forum_threads.py

# Afficher le schéma de la base Notion Leads (diagnostic, lecture seule)
py tools/inspect_leads_db.py

# Migration ponctuelle des marqueurs d'idempotence (one-shot)
py tools/migrate_notion_format.py

# Lancer les tests
py -m pytest tests/test_discord_bot.py -v
```

## Conventions

**Nommage**
- Variables et fonctions : `snake_case`
- Constantes de module : `UPPER_SNAKE_CASE` (ex. `STEAM_APP_RE`, `LEAD_TEXT_LIMIT`)
- Pas de préfixe hongrois ni d'abréviation opaque

**Format des commits**
```
type: description courte en français

# Types : feat, fix, refactor, test, docs, chore
# Exemples :
feat: détecter les liens Steam news et reconstruire l'URL de fiche jeu
fix: corriger le parsing des montants avec espaces insécables
test: couvrir les cas limites de clean_message_text
```

**Tests**
- Répertoire : `tests/`
- Un fichier par module testé : `tests/test_discord_bot.py`
- Nommage des fonctions : `test_<fonction>_<scenario>` (ex. `test_clean_message_text_texte_vide`)
- Pas de fixture fichier : les données de test sont des stubs inline (`SimpleNamespace`)

## Contraintes

- **Pas de dépendance lourde** : stdlib + `discord.py` uniquement dans les scripts. `pytest` est la seule dépendance de dev autorisée.
- **Pas d'appel réseau dans les tests** : uniquement des fonctions pures testées, aucun mock réseau, aucun appel HTTP.
- **Secrets jamais loggués** : ne pas afficher de tokens, IDs Notion ou contenu de message dans les logs ou tracebacks. Les messages d'erreur indiquent uniquement l'id Discord et le type d'erreur.
