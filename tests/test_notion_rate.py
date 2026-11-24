"""Tests de notion_rate : throttle et nouvel essai (aucun appel réseau).

L'horloge et le sommeil du throttle sont injectés ; les erreurs HTTP sont des
objets HTTPError construits en mémoire, jamais issus d'une requête.
"""

import urllib.error
from types import SimpleNamespace

import pytest

import notion_rate


def _http_error(code, retry_after=None):
    headers = {"Retry-After": retry_after} if retry_after is not None else {}
    return urllib.error.HTTPError("https://api.notion.com/v1/x", code, "err", headers, None)


class _FakeClock:
    def __init__(self):
        self.now = 100.0
        self.sleeps = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(round(seconds, 6))
        self.now += seconds


def _fake_throttle():
    pauses = []
    return SimpleNamespace(wait=lambda: None, pause=pauses.append, pauses=pauses)


# --- retry_delay / is_retryable ----------------------------------------------

def test_retry_delay_respecte_retry_after():
    assert notion_rate.retry_delay(1, "2") == 2.0
    assert notion_rate.retry_delay(4, "0.5") == 0.5


def test_retry_delay_backoff_exponentiel_sans_retry_after():
    assert [notion_rate.retry_delay(n) for n in (1, 2, 3, 4)] == [1.0, 2.0, 4.0, 8.0]


def test_retry_delay_retry_after_illisible_retombe_sur_backoff():
    assert notion_rate.retry_delay(3, "Wed, 21 Oct 2026 07:28:00 GMT") == 4.0


def test_retry_delay_plafonne():
    assert notion_rate.retry_delay(1, "3600") == notion_rate.MAX_DELAY_SECONDS
    assert notion_rate.retry_delay(20) == notion_rate.MAX_DELAY_SECONDS


def test_is_retryable():
    assert notion_rate.is_retryable(429)
    assert notion_rate.is_retryable(500) and notion_rate.is_retryable(503)
    assert not notion_rate.is_retryable(400)
    assert not notion_rate.is_retryable(404)


# --- Throttle ----------------------------------------------------------------

def test_throttle_espace_les_requetes():
    fake = _FakeClock()
    throttle = notion_rate.Throttle(per_second=2, clock=fake.clock, sleep=fake.sleep)
    throttle.wait()
    throttle.wait()
    throttle.wait()
    assert fake.sleeps == [0.5, 0.5]


def test_throttle_pas_d_attente_si_creneau_libre():
    fake = _FakeClock()
    throttle = notion_rate.Throttle(per_second=2, clock=fake.clock, sleep=fake.sleep)
    throttle.wait()
    fake.now += 10
    throttle.wait()
    assert fake.sleeps == []


def test_throttle_pause_retarde_la_requete_suivante():
    fake = _FakeClock()
    throttle = notion_rate.Throttle(per_second=3, clock=fake.clock, sleep=fake.sleep)
    throttle.wait()
    throttle.pause(5)
    throttle.wait()
    assert fake.sleeps == [5.0]


def test_throttle_par_defaut_trois_par_seconde():
    assert notion_rate.REQUESTS_PER_SECOND == 3


# --- call_with_retry ---------------------------------------------------------

def test_call_with_retry_reussit_apres_429():
    errors = [_http_error(429, "2"), _http_error(429, "2")]

    def send():
        if errors:
            raise errors.pop(0)
        return {"ok": True}

    throttle = _fake_throttle()
    logs = []
    assert notion_rate.call_with_retry(send, "PATCH", throttle=throttle, log=logs.append) == {"ok": True}
    assert throttle.pauses == [2.0, 2.0]
    assert len(logs) == 2 and "HTTP 429" in logs[0]


def test_call_with_retry_5xx_backoff_puis_abandon():
    calls = []

    def send():
        calls.append(1)
        raise _http_error(503)

    throttle = _fake_throttle()
    with pytest.raises(urllib.error.HTTPError) as info:
        notion_rate.call_with_retry(send, "GET", throttle=throttle, log=lambda m: None)
    assert info.value.code == 503
    assert len(calls) == notion_rate.MAX_ATTEMPTS == 5
    assert throttle.pauses == [1.0, 2.0, 4.0, 8.0]


def test_call_with_retry_400_non_reessaye():
    calls = []

    def send():
        calls.append(1)
        raise _http_error(400)

    with pytest.raises(urllib.error.HTTPError):
        notion_rate.call_with_retry(send, "POST", throttle=_fake_throttle(), log=lambda m: None)
    assert len(calls) == 1


def test_call_with_retry_passe_par_le_throttle_a_chaque_essai():
    waits = []
    errors = [_http_error(500)]

    def send():
        if errors:
            raise errors.pop(0)
        return 1

    throttle = SimpleNamespace(wait=lambda: waits.append(1), pause=lambda s: None)
    notion_rate.call_with_retry(send, "GET", throttle=throttle, log=lambda m: None)
    assert len(waits) == 2


def test_call_with_retry_log_sans_id():
    errors = [_http_error(429, "1")]

    def send():
        if errors:
            raise errors.pop(0)
        return 1

    logs = []
    notion_rate.call_with_retry(send, "PATCH", throttle=_fake_throttle(), log=logs.append)
    assert logs == ["Notion PATCH → HTTP 429, nouvel essai 2/5 dans 1.0s."]


# --- redact_ids --------------------------------------------------------------

def test_redact_ids_uuid_compact_et_avec_tirets():
    assert notion_rate.redact_ids("/pages/0123456789abcdef0123456789abcdef") == "/pages/<id>"
    assert notion_rate.redact_ids("/blocks/01234567-89ab-cdef-0123-456789abcdef/children") == "/blocks/<id>/children"


def test_redact_ids_garde_les_id_discord():
    assert notion_rate.redact_ids("thread 1571824726165164173") == "thread 1571824726165164173"


def test_redact_ids_none():
    assert notion_rate.redact_ids(None) == ""
