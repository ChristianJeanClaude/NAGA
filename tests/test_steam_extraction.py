"""Tests d'extraction d'App ID Steam depuis du texte libre (posts sociaux)."""

from services.steam import extract_app_id


def test_extract_from_url_in_text():
    text = "Check my game! https://store.steampowered.com/app/1234567/My_Game/"
    assert extract_app_id(text) == 1234567


def test_extract_from_selftext():
    selftext = "We just launched on Steam: store.steampowered.com/app/9876543/"
    assert extract_app_id(selftext) == 9876543


def test_extract_no_steam_link():
    text = "Just a post with no Steam link"
    assert extract_app_id(text) is None
