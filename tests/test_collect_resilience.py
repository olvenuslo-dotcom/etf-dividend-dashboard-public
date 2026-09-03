# -*- coding: utf-8 -*-
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from collect import (
    build_session,
    ensure_collection_complete,
    parse_total_count,
    rolling_year_start,
    validate_date_range,
)


def test_build_session_has_bounded_http_retries():
    session = build_session()
    retries = session.get_adapter("https://").max_retries

    assert retries.total == 3
    assert 429 in retries.status_forcelist
    assert 503 in retries.status_forcelist


def test_parse_total_count_rejects_missing_list_count():
    with pytest.raises(ValueError, match="LIST_CNT"):
        parse_total_count("<root><data /></root>")


def test_parse_total_count_returns_integer():
    assert parse_total_count('<root><LIST_CNT value="3334" /></root>') == 3334


@pytest.mark.parametrize(
    ("from_dt", "to_dt"),
    [
        ("2026-01-01", "20260131"),
        ("20260230", "20260301"),
        ("20260201", "20260131"),
    ],
)
def test_validate_date_range_rejects_bad_format_impossible_or_reversed_dates(from_dt, to_dt):
    with pytest.raises(ValueError, match="날짜"):
        validate_date_range(from_dt, to_dt)


def test_ensure_collection_complete_rejects_early_empty_page():
    with pytest.raises(RuntimeError, match="불완전"):
        ensure_collection_complete(received=60, expected=100)


def test_ensure_collection_complete_accepts_full_raw_count():
    ensure_collection_complete(received=100, expected=100, unique=100)


def test_ensure_collection_complete_rejects_repeated_pages():
    with pytest.raises(RuntimeError, match="불완전"):
        ensure_collection_complete(received=100, expected=100, unique=30)


def test_rolling_year_start_handles_leap_day():
    from datetime import date

    assert rolling_year_start(date(2024, 2, 29)) == "20230228"
