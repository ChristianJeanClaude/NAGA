"""Scraping Gamalytic pour récupérer les wishlists des jeux non sortis."""

import logging
import os
import shutil

from playwright.async_api import async_playwright

logger = logging.getLogger(__name__)

GAMALYTIC_URL = "https://gamalytic.com/game/{app_id}"


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
            # Sur Railway/Nix, Chromium est fourni à un chemin système : on le
            # détecte plutôt que d'utiliser le binaire téléchargé par Playwright
            # (qui ne trouve pas ses .so sous Nix).
            chromium_path = (
                shutil.which("chromium")
                or shutil.which("chromium-browser")
                or shutil.which("google-chrome")
                or os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH")
            )

            launch_kwargs = {
                "headless": True,
                "args": [
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                ],
            }
            if chromium_path:
                launch_kwargs["executable_path"] = chromium_path

            browser = await playwright.chromium.launch(**launch_kwargs)
            try:
                context = await browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/125.0.0.0 Safari/537.36"
                    ),
                    viewport={"width": 1280, "height": 720},
                )
                page = await context.new_page()
                await page.add_init_script(
                    "Object.defineProperty(navigator, 'webdriver', "
                    "{get: () => undefined})"
                )
                await page.goto(
                    url, wait_until="domcontentloaded", timeout=30000
                )
                await page.wait_for_timeout(8000)
                text = await page.inner_text("body")
            finally:
                await browser.close()
    except Exception:
        logger.error(
            "Échec du scraping Gamalytic (app_id=%s)", app_id, exc_info=True
        )
        return empty

    result = dict(empty)
    lines = text.split("\n")
    for line in lines:
        line = line.strip()
        if "Outstanding wishlists:" in line:
            val = line.replace("Outstanding wishlists:", "").strip()
            result["wishlists"] = _parse_number(val)
        elif "Daily wishlist additions:" in line:
            val = line.replace("Daily wishlist additions:", "").strip()
            result["daily_additions"] = _parse_number(val)
        elif line.startswith("Followers:") and result["followers"] is None:
            val = line.replace("Followers:", "").strip()
            result["followers"] = _parse_number(val)
    return result


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
