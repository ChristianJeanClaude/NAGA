# hello-naga-v2

Outils internes NAGA : analyse de relevés bancaires et collecte de leads de jeux
depuis Discord vers Notion.

## Structure du projet

```
hello-naga-v2/
├── bots/
│   └── discord_bot.py          # Bot Discord (lecture seule) → Notion
├── tools/
│   └── migrate_notion_format.py# Scripts de migration Notion ponctuels
├── releves/                    # Relevés bancaires NAGA au format CSV
│   └── releve_naga_YYYY_MM.csv
├── tests/                      # Tests pytest
│   ├── test_discord_bot.py
│   └── test_categories.py
├── naga_releves.py             # Analyse des relevés : dépenses/revenus par mois
├── bank_expenses.py            # Script initial (format simplifié, référence)
├── categories.py               # Catégorisation des libellés bancaires
├── conftest.py                 # Rend bots/ importable par les tests
├── CLAUDE.md                   # Conventions & contraintes du projet
└── README.md
```

## Analyse de relevés bancaires

Lit tous les CSV de `releves/`, regroupe par mois et affiche dépenses, revenus et
solde net.

```bash
python naga_releves.py
```

Stack : Python 3.7+, stdlib uniquement (`csv`, `pathlib`, `collections`). Voir
`CLAUDE.md` pour le format des fichiers et les règles de catégorisation.

## Bot Discord → Notion (`bots/discord_bot.py`)

Bot **lecture seule** qui capture les messages d'un channel forum Discord et les
publie dans une base Notion (une page par jeu).

- **Démarrage** : scrape tout l'historique (posts de forum actifs et archivés).
- **Temps réel** : écoute les nouveaux messages et nouveaux posts.
- **Par message** : extrait texte, liens, pièces jointes, auteur, date.
- **Routage** : chaque **thread** du forum crée sa propre page Notion (titre =
  nom du thread) ; tous ses messages y sont rattachés, avec ou sans lien Steam.
  Les messages hors-thread vont dans la page « Splash Divers ».
- **Idempotence** : les `message_id` déjà traités sont stockés dans la propriété
  Notion `Message IDs` (rich_text) — aucun doublon au redémarrage.

#### Seconde base Notion (Leads)

En parallèle, le bot alimente une **seconde base Notion** (CRM de leads, module
`bots/notion_leads.py`) via `push_to_notion`. Chaque **thread** = une ligne de lead,
avec la **conversation complète** du thread agrégée (champs `Nom du jeu`, `Source`,
`Date`, `Messages`, `Liens`, `Pièces jointes`, `Statut`). Les messages hors-thread
ne sont pas poussés. Le token est lu dans `NOTION_TOKEN_LEADS` : si la clé est
absente, ce push est simplement désactivé (la base principale fonctionne quand même).

### Configuration

Secrets jamais hardcodés, lus depuis le dossier personnel (`~`) :

| Fichier          | Clés                                                          |
|------------------|---------------------------------------------------------------|
| `~/.env.discord` | `DISCORD_TOKEN`, `DISCORD_CHANNEL_ID`                         |
| `~/.env.notion`  | `NOTION_TOKEN`, `NOTION_PARENT_PAGE_ID`, `NOTION_TOKEN_LEADS` |

`NOTION_TOKEN_LEADS` (optionnel) active le push vers la seconde base Notion (Leads).

`NOTION_PARENT_PAGE_ID` peut désigner soit une page parente (le bot crée la base
« NAGA — Jeux Discord » dessous), soit directement une database existante (le bot
l'adopte et détecte sa propriété titre).

Prérequis : `pip install discord.py`, intent **MESSAGE CONTENT** activé, et
intégration Notion partagée avec la page/base cible.

### Lancement

```bash
python bots/discord_bot.py
```

## Outils de migration (`tools/`)

Scripts ponctuels qui agissent sur la base Notion. Ils importent le bot depuis
`bots/` (via ajustement du `sys.path`) et se lancent **depuis la racine** :

```bash
python tools/migrate_notion_format.py --dry-run   # aperçu, ne modifie rien
python tools/migrate_notion_format.py             # applique
```

`migrate_notion_format.py` retire les anciens marqueurs `⟦msg:ID⟧` du corps des
pages et reporte les id dans la propriété `Message IDs`. Ré-exécutable sans risque.

## Tests

```bash
python -m pytest
```

`conftest.py` ajoute `bots/` au `sys.path`. Les tests n'effectuent **aucun appel
réseau** (contrainte projet) : seules les fonctions pures sont testées.
