"""Tests de services/gamalytic.py — parsing des nombres scrapés."""

from services.gamalytic import _parse_number


def test_parse_number_k():
    assert _parse_number("1.2k") == 1200
    assert _parse_number("10.5k") == 10500


def test_parse_number_plain():
    assert _parse_number("432") == 432
    assert _parse_number("1,234") == 1234


def test_parse_number_invalid():
    assert _parse_number("N/A") is None
    assert _parse_number("") is None
