"""Modèle de données représentant un jeu scouté.

`GameData` regroupe tous les champs normalisés collectés depuis différentes
sources (API Steam, scraping de la page boutique, SteamSpy, contexte Discord).
La méthode `to_notion_properties()` traduit l'objet en dictionnaire de
propriétés prêt à être envoyé à l'API Notion, en omettant les champs absents.
"""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(kw_only=True)
class GameData:
    """Données normalisées d'un jeu, agrégées depuis toutes les sources."""

    # --- Identifiers ---
    app_id: int
    steam_url: str

    # --- Steam API ---
    name: str
    short_description: str
    release_date: str | None  # raw string from API e.g. "21 Sep, 2023"
    developer: str
    publisher: str
    price_eur: float | None = None  # None if free or not available
    platforms: list[str] = field(default_factory=list)  # e.g. ["Windows", "Mac"]
    genres: list[str] = field(default_factory=list)  # e.g. ["Action", "Indie"]
    website: str | None = None

    # --- Steam API (enrichissement) ---
    screenshots: list[str] = field(default_factory=list)  # URLs CDN (3 max)
    trailer: str | None = None  # URL webm.max ou mp4.max du 1er film

    # --- Scraping (store page) ---
    tags: list[str] = field(default_factory=list)  # top 5 community tags
    review_score: str | None = None  # e.g. "Très positif", "Mixte"
    review_count: int | None = None
    discord_url: str | None = None
    twitter_url: str | None = None

    # --- SteamSpy ---
    owners_estimate: str | None = None  # e.g. "500,000 .. 1,000,000"
    peak_ccu: int | None = None
    avg_playtime_minutes: int | None = None
    followers: int | None = None

    # --- Discord context ---
    scouted_by: str  # Discord username
    scouted_at: str  # ISO 8601 timestamp
    discord_message_url: str
    attachments: list[str] = field(default_factory=list)  # URLs des pièces jointes

    # --- Computed ---
    relevance_score: int = 0  # 0-100, voir services.scoring

    @staticmethod
    def _parse_steam_date(raw: str | None) -> str | None:
        """Convertit une date Steam en ISO 8601 ("YYYY-MM-DD").

        Tente plusieurs formats renvoyés par Steam, du plus précis au plus
        vague, avec repli sur le 1er du mois ou de l'année. Retourne ``None``
        pour les formats non reconnus (ex. "Coming soon", "À venir") ou une
        entrée vide.
        """
        if not raw:
            return None

        candidate = raw.strip()
        # (format strptime, granularité) du plus précis au plus vague.
        formats = (
            "%d %b, %Y",  # "21 Sep, 2023"
            "%d %B, %Y",  # "21 September, 2023"
            "%b %Y",      # "Sep 2023"      → 1er du mois
            "%B %Y",      # "September 2023" → 1er du mois
            "%Y",         # "2023"          → 1er janvier
        )
        for fmt in formats:
            try:
                parsed = datetime.strptime(candidate, fmt)
            except ValueError:
                continue
            return parsed.strftime("%Y-%m-%d")
        return None

    def to_notion_properties(self) -> dict:
        """Formate les champs pour l'API Notion, en omettant les valeurs absentes.

        Les champs valant ``None`` ou une liste vide sont ignorés. Les nombres
        valant ``0`` sont conservés (seul ``None`` est considéré comme absent).
        """
        props: dict = {}

        def add_title(name: str, value: str | None) -> None:
            if value:
                props[name] = {"title": [{"text": {"content": value}}]}

        def add_text(name: str, value: str | None) -> None:
            if value:
                props[name] = {"rich_text": [{"text": {"content": value}}]}

        def add_number(name: str, value: float | None) -> None:
            if value is not None:
                props[name] = {"number": value}

        def add_select(name: str, value: str | None) -> None:
            if value:
                props[name] = {"select": {"name": value}}

        def add_multi_select(name: str, value: list[str]) -> None:
            if value:
                props[name] = {"multi_select": [{"name": v} for v in value]}

        def add_url(name: str, value: str | None) -> None:
            if value:
                props[name] = {"url": value}

        def add_date(name: str, value: str | None) -> None:
            if value:
                props[name] = {"date": {"start": value}}

        # --- Identifiers ---
        add_title("Game", self.name)
        add_number("Steam App ID", self.app_id)
        add_url("Steam URL", self.steam_url)

        # --- Steam API ---
        add_text("Description", self.short_description)
        add_text("Developer", self.developer)
        add_multi_select("Genres", self.genres)
        add_url("Website", self.website)
        # release_date est brut ("21 Sep, 2023") : on le convertit en ISO 8601.
        add_date("Release Date", self._parse_steam_date(self.release_date))

        # --- Steam API (enrichissement) ---
        # URLs des captures d'écran, une par ligne dans un champ texte.
        add_text("Screenshots", "\n".join(self.screenshots))
        add_url("Trailer", self.trailer)

        # --- Scraping (store page) ---
        add_multi_select("Tags", self.tags)
        add_select("Review Score", self.review_score)
        add_number("Review Count", self.review_count)
        add_url("Twitter URL", self.twitter_url)
        add_url("Discord URL", self.discord_url)

        # --- SteamSpy ---
        add_text("Owners Estimate", self.owners_estimate)
        add_number("Peak CCU", self.peak_ccu)
        add_number("Followers", self.followers)

        # --- Discord context ---
        add_text("Scouted By", self.scouted_by)
        add_date("Scouted At", self.scouted_at)
        # URLs des pièces jointes Discord, une par ligne dans un champ texte.
        add_text("Attachments", "\n".join(self.attachments))

        # --- Computed ---
        if self.relevance_score:
            add_number("Relevance Score", self.relevance_score)

        return props
