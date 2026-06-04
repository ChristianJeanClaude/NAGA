"""Calcul d'un score de pertinence (0-100) orienté scouting pré-lancement.

Contrairement à un scoring purement post-lancement (avis, propriétaires, CCU),
ce barème valorise d'abord les signaux disponibles avant la sortie d'un jeu —
abonnés Steam, adéquation des genres/tags au profil NAGA, présence de liens
sociaux, date de sortie connue — tout en conservant les métriques
post-lancement lorsqu'elles existent. Voir ``compute_relevance_score`` pour le
barème détaillé.
"""

import re

from models.game import GameData

# Capture la première suite de chiffres (avec virgules) d'une chaîne.
_NUMBER_RE = re.compile(r"[\d,]+")

# Genres et tags valorisés (profil NAGA : indé, cosy, narratif…).
_NAGA_GENRES = ["Indie", "Adventure", "Casual", "RPG", "Simulation", "Puzzle"]
_NAGA_TAGS = [
    "Cute",
    "Relaxing",
    "Cozy",
    "Singleplayer",
    "Hand-drawn",
    "Colorful",
    "Cartoony",
    "Atmospheric",
    "Story Rich",
    "2D",
]

_REVIEW_SCORE_POINTS = {
    "Overwhelmingly Positive": 20,
    "Very Positive": 16,
    "Mostly Positive": 12,
    "Mixed": 6,
}


# --- Signaux pré-lancement ---


def _score_followers(followers: int | None) -> int:
    """Abonnés Steam (30 pts) : un proxy d'intérêt avant la sortie."""
    if not followers:
        return 0
    if followers >= 10_000:
        return 30
    if followers >= 5_000:
        return 22
    if followers >= 2_000:
        return 15
    if followers >= 500:
        return 8
    return 3


def _score_genres(genres: list[str]) -> int:
    """Genres (25 pts) : +5 par genre du profil NAGA, plafonné à 25."""
    matches = sum(1 for genre in genres if genre in _NAGA_GENRES)
    return min(25, matches * 5)


def _score_tags(tags: list[str]) -> int:
    """Tags (20 pts) : +4 par tag du profil NAGA, plafonné à 20."""
    matches = sum(1 for tag in tags if tag in _NAGA_TAGS)
    return min(20, matches * 4)


def _score_social(discord_url: str | None, twitter_url: str | None) -> int:
    """Liens sociaux (15 pts) : Discord +8, Twitter +7."""
    score = 0
    if discord_url:
        score += 8
    if twitter_url:
        score += 7
    return score


def _score_release_date(release_date: str | None) -> int:
    """Date de sortie connue (10 pts) : +10 si parsable, 0 si "Coming soon"/None."""
    return 10 if GameData._parse_steam_date(release_date) else 0


# --- Signaux post-lancement ---


def _score_review(review_score: str | None) -> int:
    """Qualité des avis (20 pts) selon le libellé global Steam."""
    return _REVIEW_SCORE_POINTS.get(review_score or "", 0)


def _parse_owners_lower_bound(owners: str | None) -> int | None:
    """Extrait la borne inférieure d'une plage "500,000 .. 1,000,000"."""
    if not owners:
        return None
    match = _NUMBER_RE.search(owners)
    if not match:
        return None
    try:
        return int(match.group(0).replace(",", ""))
    except ValueError:
        return None


def _score_owners(owners: str | None) -> int:
    """Propriétaires estimés (15 pts) selon la borne inférieure de la plage."""
    lower = _parse_owners_lower_bound(owners)
    if lower is None:
        return 0
    if lower >= 1_000_000:
        return 15
    if lower >= 500_000:
        return 12
    if lower >= 200_000:
        return 9
    if lower >= 100_000:
        return 6
    if lower >= 50_000:
        return 3
    return 0


def _score_peak_ccu(peak_ccu: int | None) -> int:
    """Pic de joueurs simultanés (10 pts)."""
    if not peak_ccu:
        return 0
    if peak_ccu >= 50_000:
        return 10
    if peak_ccu >= 10_000:
        return 8
    if peak_ccu >= 5_000:
        return 5
    if peak_ccu >= 1_000:
        return 3
    return 0


def _score_review_count(review_count: int | None) -> int:
    """Volume d'avis (10 pts)."""
    if not review_count:
        return 0
    if review_count >= 10_000:
        return 10
    if review_count >= 5_000:
        return 8
    if review_count >= 1_000:
        return 5
    if review_count >= 500:
        return 3
    return 0


def compute_relevance_score(game: GameData) -> int:
    """Calcule un score de pertinence (0-100) orienté scouting.

    Le score combine des signaux pré-lancement (toujours disponibles dès
    l'annonce d'un jeu) et des signaux post-lancement (présents seulement une
    fois le jeu sorti). Le total est plafonné à 100.

    Signaux pré-lancement :
    - Followers (30) : >=10k=30, >=5k=22, >=2k=15, >=500=8, >0=3, None=0.
    - Genres (25) : +5 par genre du profil NAGA, plafonné à 25.
    - Tags (20) : +4 par tag du profil NAGA, plafonné à 20.
    - Liens sociaux (15) : Discord présent +8, Twitter présent +7.
    - Release Date connue (10) : date parsable +10, "Coming soon"/None=0.

    Signaux post-lancement :
    - Review Score (20) : Overwhelmingly Positive=20, Very Positive=16,
      Mostly Positive=12, Mixed=6, sinon/None=0.
    - Owners Estimate (15) — borne inférieure : >=1M=15, >=500k=12, >=200k=9,
      >=100k=6, >=50k=3, sinon/None=0.
    - Peak CCU (10) : >=50k=10, >=10k=8, >=5k=5, >=1k=3, sinon/None=0.
    - Review Count (10) : >=10k=10, >=5k=8, >=1k=5, >=500=3, sinon/None=0.

    Le résultat est ``min(100, somme)`` : le plafonnement évite les dépassements
    quand de nombreux signaux post-lancement s'accumulent.
    """
    total = (
        _score_followers(game.followers)
        + _score_genres(game.genres)
        + _score_tags(game.tags)
        + _score_social(game.discord_url, game.twitter_url)
        + _score_release_date(game.release_date)
        + _score_review(game.review_score)
        + _score_owners(game.owners_estimate)
        + _score_peak_ccu(game.peak_ccu)
        + _score_review_count(game.review_count)
    )
    return min(100, total)
