"""Scraping Gamalytic pour récupérer les wishlists des jeux non sortis."""

import logging
import re

from playwright.async_api import async_playwright

logger = logging.getLogger(__name__)

GAMALYTIC_URL = "https://gamalytic.com/game/{app_id}"

# Mots-clés (insensibles à la casse) cherchés dans le texte de la page pour
# rattacher une valeur numérique à chaque métrique.
_METRIC_KEYWORDS = {
    "wishlists": ("wishlist",),
    "daily_additions": ("daily addition", "daily wishlist", "daily"),
    "followers": ("follower",),
}


async def get_wishlist_data(app_id: int) -> dict:
    """Scrape la page Gamalytic d'un jeu pour récupérer ses métriques.

    Retourne un dict :
    - ``wishlists``: int | None  (ex: 1200 pour "1.2k")
    - ``daily_additions``: int | None
    - ``followers``: int | None

    Best-effort : retourne ``{"wishlists": None, "daily_additions": None,
    "followers": None}`` sur n'importe quelle erreur — ne lève jamais.
    """
    empty = {"wishlists": None, "daily_additions": None, "followers": None}
    url = GAMALYTIC_URL.format(app_id=app_id)
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.goto(url)
                await page.wait_for_timeout(5000)
                body_text = await page.inner_text("body")
            finally:
                await browser.close()
    except Exception:
        logger.error(
            "Échec du scraping Gamalytic (app_id=%s)", app_id, exc_info=True
        )
        return empty

    result = dict(empty)
    for metric, keywords in _METRIC_KEYWORDS.items():
        result[metric] = _extract_metric(body_text, keywords)
    return result


def _extract_metric(text: str, keywords: tuple[str, ...]) -> int | None:
    """Cherche un nombre adjacent à l'un des ``keywords`` dans ``text``.

    Examine chaque ligne contenant un mot-clé et renvoie le premier nombre
    parseable trouvé sur cette ligne. ``None`` si rien n'est trouvé.
    """
    if not text:
        return None
    for line in text.splitlines():
        lowered = line.lower()
        if not any(keyword in lowered for keyword in keywords):
            continue
        for token in re.findall(r"[\d.,]+\s*[kKmM]?", line):
            value = _parse_number(token)
            if value is not None:
                return value
    return None


def _parse_number(text: str) -> int | None:
    """Parse un nombre depuis du texte Gamalytic.

    - "1.2k" → 1200
    - "10.5k" → 10500
    - "1,234" → 1234
    - "432" → 432
    - Retourne ``None`` si non parseable.
    """
    if not text:
        return None

    cleaned = text.strip().lower().replace(",", "").replace(" ", "")
    if not cleaned:
        return None

    multiplier = 1
    if cleaned.endswith("k"):
        multiplier = 1_000
        cleaned = cleaned[:-1]
    elif cleaned.endswith("m"):
        multiplier = 1_000_000
        cleaned = cleaned[:-1]

    try:
        value = float(cleaned)
    except ValueError:
        return None
    return int(value * multiplier)
