"""Service d'accès à l'API et aux pages Steam (+ SteamSpy).

Récupère et agrège toutes les données d'un jeu depuis trois sources :

1. l'API ``appdetails`` de Steam (métadonnées officielles, prix, plateformes) ;
2. le scraping de la page boutique (tags communautaires, avis, liens sociaux) ;
3. l'API SteamSpy (estimation de propriétaires, pic de joueurs, temps de jeu).

Tous les appels HTTP sont asynchrones (``aiohttp``) ; le scraping utilise
``BeautifulSoup``. La fonction d'orchestration ``fetch_game_data`` fusionne le
tout dans un objet :class:`~models.game.GameData`.
"""

import asyncio
import re
from urllib.parse import parse_qs, unquote, urlparse

import aiohttp
from bs4 import BeautifulSoup

from models.game import GameData
from services.retry import with_retry

STEAM_API_URL = "https://store.steampowered.com/api/appdetails"
STEAM_STORE_URL = "https://store.steampowered.com/app/{app_id}/"
STEAMSPY_API_URL = "https://steamspy.com/api.php"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

# Cookies pour contourner la barrière d'âge des pages boutique (contenu mature).
_AGE_GATE_COOKIES = {
    "birthtime": "568022401",  # 1 janv. 1988
    "mature_content": "1",
    "lastagecheckage": "1-January-1988",
}

# ``\d+`` s'arrête au premier caractère non numérique (``/`` ou ``?``), donc
# le slug, le slash final, les query params et les fragments sont ignorés.
_APP_ID_RE = re.compile(r"/app/(\d+)")

# "12,345 followers" → capture le nombre (avec séparateurs de milliers).
_FOLLOWERS_RE = re.compile(r"([\d,]+)\s+followers", re.IGNORECASE)


def extract_app_id(url: str) -> int | None:
    """Extrait l'App ID Steam d'une URL de page boutique.

    Seul l'identifiant numérique est extrait, quel que soit ce qui le suit
    (slug, slash final, query params ``?l=french``, fragments…). Exemples :
    - ``https://store.steampowered.com/app/1234567/Game_Name/``
    - ``https://store.steampowered.com/app/1234567``
    - ``https://store.steampowered.com/app/1234567/Game_Name/?l=french``
    - ``https://store.steampowered.com/app/1234567?curator_clanid=xxx``

    Retourne ``None`` si aucun App ID n'est trouvé.
    """
    if not url:
        return None
    match = _APP_ID_RE.search(url)
    return int(match.group(1)) if match else None


def _decode_steam_link(href: str) -> str:
    """Décode un lien social encapsulé par le redirecteur Steam.

    Steam enveloppe les liens externes des pages boutique dans une URL de
    redirection ``https://steamcommunity.com/linkfilter/?u=<url_encodée>``.
    Cette fonction extrait le paramètre ``u`` et le décode pour retrouver
    l'URL directe (ex. ``https://discord.gg/abc``). Si ``href`` n'est pas un
    lien ``linkfilter`` (ou est vide), il est retourné tel quel.
    """
    if not href or "/linkfilter/" not in href:
        return href
    target = parse_qs(urlparse(href).query).get("u")
    return unquote(target[0]) if target else href


async def fetch_game_data(
    app_id: int,
    scouted_by: str,
    scouted_at: str,
    discord_message_url: str,
) -> GameData:
    """Orchestre la récupération des données et retourne un ``GameData`` peuplé.

    Appelle dans l'ordre ``_fetch_steam_api``, ``_scrape_store_page`` puis
    ``_fetch_steamspy``, et fusionne les résultats. Une erreur de l'API Steam
    (jeu introuvable) est propagée ; le scraping et SteamSpy sont en
    « best-effort » : leur échec laisse les champs correspondants vides plutôt
    que de faire échouer tout le scouting.
    """
    async with aiohttp.ClientSession(headers=HEADERS) as session:
        api_data = await _fetch_steam_api(app_id, session)

        try:
            scrape_data = await _scrape_store_page(app_id, session)
        except (aiohttp.ClientError, asyncio.TimeoutError):
            scrape_data = {}

        try:
            steamspy_data = await _fetch_steamspy(app_id, session)
        except (aiohttp.ClientError, asyncio.TimeoutError):
            steamspy_data = {}

    return GameData(
        app_id=app_id,
        steam_url=STEAM_STORE_URL.format(app_id=app_id),
        scouted_by=scouted_by,
        scouted_at=scouted_at,
        discord_message_url=discord_message_url,
        **api_data,
        **scrape_data,
        **steamspy_data,
    )


async def _get_json(
    session: aiohttp.ClientSession,
    url: str,
    *,
    params: dict | None = None,
    cookies: dict | None = None,
):
    """GET unique renvoyant le JSON décodé. ``raise_for_status`` lève sur 4xx/5xx
    (dont 429), exposant une ``aiohttp.ClientError`` que ``with_retry`` peut
    intercepter."""
    async with session.get(url, params=params, cookies=cookies) as response:
        response.raise_for_status()
        # content_type=None : SteamSpy renvoie un Content-Type non standard.
        return await response.json(content_type=None)


async def _get_text(
    session: aiohttp.ClientSession,
    url: str,
    *,
    params: dict | None = None,
    cookies: dict | None = None,
) -> str:
    """GET unique renvoyant le corps en texte (pour le scraping HTML)."""
    async with session.get(url, params=params, cookies=cookies) as response:
        response.raise_for_status()
        return await response.text()


async def _fetch_steam_api(app_id: int, session: aiohttp.ClientSession) -> dict:
    """Appelle l'API ``appdetails`` de Steam et retourne un dict aplati.

    Params : ``appids``, ``cc=fr``, ``l=english``. Lève ``ValueError`` si le jeu
    est introuvable ou si ``success`` est faux. Réessaie jusqu'à 3 fois avec
    backoff exponentiel de 2s sur HTTP 429 ou erreur de connexion.
    """
    params = {"appids": str(app_id), "cc": "fr", "l": "english"}
    payload = await with_retry(
        _get_json,
        session,
        STEAM_API_URL,
        params=params,
        exceptions=(aiohttp.ClientError,),
        label="Steam API",
    )

    entry = payload.get(str(app_id)) if payload else None
    if not entry or not entry.get("success") or "data" not in entry:
        # Pas de données financières/sensibles dans le message d'erreur.
        raise ValueError(f"Steam app not found or unavailable: app_id={app_id}")

    data = entry["data"]

    # Prix : None si gratuit ou indisponible, sinon en euros.
    price_eur: float | None = None
    if not data.get("is_free", False):
        price_overview = data.get("price_overview")
        if price_overview and "final" in price_overview:
            price_eur = price_overview["final"] / 100

    platform_flags = data.get("platforms", {})
    platforms = [
        label
        for key, label in (("windows", "Windows"), ("mac", "Mac"), ("linux", "Linux"))
        if platform_flags.get(key)
    ]

    developers = data.get("developers") or []
    publishers = data.get("publishers") or []

    return {
        "name": data.get("name", ""),
        "short_description": data.get("short_description", ""),
        "release_date": (data.get("release_date") or {}).get("date"),
        "developer": developers[0] if developers else "",
        "publisher": publishers[0] if publishers else "",
        "price_eur": price_eur,
        "platforms": platforms,
        "genres": [g["description"] for g in data.get("genres", [])],
        "website": data.get("website"),
        "trailer": _extract_trailer(data),
    }


def _extract_trailer(data: dict) -> str | None:
    """Retourne l'URL du premier film, en préférant le WebM puis le MP4.

    Lit ``data["movies"]`` et renvoie ``webm.max`` ou, à défaut, ``mp4.max`` du
    premier film. Retourne ``None`` si aucun film exploitable n'est présent.
    """
    movies = data.get("movies") or []
    if not movies or not isinstance(movies[0], dict):
        return None
    first = movies[0]
    webm = first.get("webm") or {}
    mp4 = first.get("mp4") or {}
    return webm.get("max") or mp4.get("max") or None


def _extract_follower_count(soup: BeautifulSoup) -> int | None:
    """Extrait le nombre de followers de la page boutique (best-effort).

    Steam affiche le compteur dans la ``followsection`` sous la forme
    « 12,345 followers ». Plusieurs sélecteurs sont essayés en cascade, du plus
    spécifique au plus large :

    1. ``div.followsection span`` ;
    2. tout ``span``/``div`` dont le texte contient « followers » près d'un
       nombre (motif ``[\\d,]+ followers``).

    Le nombre est nettoyé (virgules retirées) puis converti en ``int``. Toute
    erreur — sélecteur absent, texte non conforme — renvoie ``None`` ; la
    fonction ne lève jamais.
    """
    try:
        candidates = [
            el.get_text(" ", strip=True)
            for el in soup.select("div.followsection span")
        ]
        candidates.extend(
            el.get_text(" ", strip=True)
            for el in soup.find_all(["span", "div"])
            if "followers" in el.get_text().lower()
        )

        for text in candidates:
            match = _FOLLOWERS_RE.search(text)
            if not match:
                continue
            try:
                return int(match.group(1).replace(",", ""))
            except ValueError:
                continue
    except Exception:
        # Best-effort : ne jamais faire échouer le scraping pour les followers.
        return None
    return None


async def _scrape_store_page(app_id: int, session: aiohttp.ClientSession) -> dict:
    """Scrape la page boutique : tags, avis, liens sociaux (best-effort).

    Les sélecteurs HTML de Steam peuvent changer ; chaque extraction est isolée
    pour qu'un champ manquant n'empêche pas les autres. Les champs introuvables
    valent ``None`` ou une liste vide.
    """
    url = STEAM_STORE_URL.format(app_id=app_id)
    html = await with_retry(
        _get_text,
        session,
        url,
        cookies=_AGE_GATE_COOKIES,
        exceptions=(aiohttp.ClientError,),
        label="Steam store page",
    )

    soup = BeautifulSoup(html, "html.parser")

    # Top 5 tags communautaires.
    tags = [
        tag.get_text(strip=True)
        for tag in soup.select("a.app_tag")
        if tag.get_text(strip=True)
    ][:5]

    # Libellé global des avis (ex. "Very Positive") : on prend le dernier
    # `.game_review_summary` non vide, généralement le résumé « all reviews ».
    review_score: str | None = None
    summaries = [
        s.get_text(strip=True)
        for s in soup.select(".game_review_summary")
        if s.get_text(strip=True)
    ]
    if summaries:
        review_score = summaries[-1]

    # Nombre total d'avis via les métadonnées schema.org.
    review_count: int | None = None
    review_count_meta = soup.select_one('meta[itemprop="reviewCount"]')
    if review_count_meta and review_count_meta.get("content"):
        try:
            review_count = int(review_count_meta["content"])
        except ValueError:
            review_count = None

    # Liens sociaux : on balaie les ancres de la page.
    discord_url: str | None = None
    twitter_url: str | None = None
    for anchor in soup.find_all("a", href=True):
        # Steam encapsule les liens externes dans un redirecteur ; on décode
        # le paramètre ``u=`` pour stocker l'URL directe.
        href = _decode_steam_link(anchor["href"])
        if discord_url is None and ("discord.gg" in href or "discord.com" in href):
            discord_url = href
        elif twitter_url is None and ("twitter.com" in href or "x.com" in href):
            twitter_url = href
        if discord_url and twitter_url:
            break

    follower_count = _extract_follower_count(soup)

    return {
        "tags": tags,
        "review_score": review_score,
        "review_count": review_count,
        "discord_url": discord_url,
        "twitter_url": twitter_url,
        "follower_count": follower_count,
    }


async def _fetch_steamspy(app_id: int, session: aiohttp.ClientSession) -> dict:
    """Interroge SteamSpy : propriétaires estimés, pic de joueurs, temps de jeu."""
    params = {"request": "appdetails", "appid": str(app_id)}
    data = await with_retry(
        _get_json,
        session,
        STEAMSPY_API_URL,
        params=params,
        exceptions=(aiohttp.ClientError,),
        label="SteamSpy",
    )

    if not isinstance(data, dict):
        return {
            "owners_estimate": None,
            "peak_ccu": None,
            "avg_playtime_minutes": None,
            "followers": None,
        }

    owners = data.get("owners") or None
    peak_ccu = data.get("ccu")
    avg_playtime = data.get("average_forever")
    followers = data.get("followers")

    return {
        "owners_estimate": owners,
        "peak_ccu": peak_ccu if isinstance(peak_ccu, int) else None,
        "avg_playtime_minutes": avg_playtime if isinstance(avg_playtime, int) else None,
        "followers": followers if isinstance(followers, int) else None,
    }
