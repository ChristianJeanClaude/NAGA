"""Tests du filtre de récence de ScoutingJob (``_is_recent``)."""

from datetime import datetime, timedelta, timezone

from scouting.job import MAX_POST_AGE_DAYS, ScoutingJob


def _job() -> ScoutingJob:
    """Instancie ScoutingJob sans déclencher __init__ (qui dépend de l'env).

    ``_is_recent`` est une méthode pure : elle n'utilise aucun attribut
    d'instance, donc un objet nu suffit.
    """
    return ScoutingJob.__new__(ScoutingJob)


def test_is_recent_today():
    now = datetime.now(timezone.utc).timestamp()
    assert _job()._is_recent(now) is True


def test_is_recent_old():
    old = (
        datetime.now(timezone.utc) - timedelta(days=MAX_POST_AGE_DAYS + 1)
    ).timestamp()
    assert _job()._is_recent(old) is False


def test_is_recent_exactly_30_days():
    # Borne : un post créé il y a exactement MAX_POST_AGE_DAYS jours est gardé.
    # On ajoute une petite marge pour compenser le temps écoulé entre le calcul
    # du timestamp et l'appel à _is_recent (sinon le cutoff dépasse le post).
    boundary = (
        datetime.now(timezone.utc)
        - timedelta(days=MAX_POST_AGE_DAYS)
        + timedelta(seconds=1)
    ).timestamp()
    assert _job()._is_recent(boundary) is True
