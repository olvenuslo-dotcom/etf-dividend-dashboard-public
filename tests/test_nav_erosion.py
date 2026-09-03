# -*- coding: utf-8 -*-
from pathlib import Path
import sys

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nav_erosion import (
    calculate_nav_metrics,
    fetch_nav_history,
    filter_valid_isin_rows,
    isin_to_ticker,
    load_distributions_for_isin,
    public_nav_error_message,
)


def test_isin_to_ticker_extracts_krx_six_digit_code():
    assert isin_to_ticker("KR7152100004") == "152100"


def test_isin_to_ticker_accepts_new_alphanumeric_krx_code():
    assert isin_to_ticker("KR70004G0002") == "0004G0"


def test_isin_to_ticker_rejects_non_krx_etf_isin():
    with pytest.raises(ValueError, match="KRX 종목코드"):
        isin_to_ticker("US78462F1030")


@pytest.mark.parametrize(
    "bad_isin",
    [
        "KR7152100",
        "KR7152100004EXTRA",
        "KR7ABCDEF",
        "KR7152100005",
        "KR70004g0004",
        "KR7Å00000009",
        "KR7中00000002",
    ],
)
def test_isin_to_ticker_rejects_malformed_or_bad_checksum(bad_isin):
    with pytest.raises(ValueError, match="KRX 종목코드"):
        isin_to_ticker(bad_isin)


def test_filter_valid_isin_rows_drops_bad_values_without_crashing():
    frame = pd.DataFrame(
        {"ISIN": ["KR7152100004", "KR7152100005", None], "종목명": ["정상", "오류", "누락"]}
    )

    result = filter_valid_isin_rows(frame)

    assert result["ISIN"].tolist() == ["KR7152100004"]
    assert result.iloc[0]["KRX코드"] == "152100"


def test_public_nav_error_message_never_contains_exception_details():
    message = public_nav_error_message(RuntimeError("password=do-not-show"))

    assert "password" not in message
    assert "다시 시도" in message


def test_calculate_nav_metrics_combines_nav_and_distributions():
    nav = pd.DataFrame(
        {"NAV": [10000.0, 9500.0, 9200.0], "종가": [10020, 9480, 9180]},
        index=pd.to_datetime(["2026-01-02", "2026-02-02", "2026-03-02"]),
    )
    distributions = pd.DataFrame(
        {
            "지급기준일": pd.to_datetime(["2026-02-01", "2026-03-01"]),
            "주당분배금": [200.0, 250.0],
        }
    )

    result = calculate_nav_metrics(nav, distributions)

    assert result["nav_start"] == 10000.0
    assert result["nav_end"] == 9200.0
    assert result["nav_change_pct"] == pytest.approx(-8.0)
    assert result["cumulative_distribution"] == 450.0
    assert result["distribution_yield_pct"] == pytest.approx(4.5)
    assert result["total_return_proxy_pct"] == pytest.approx(-3.5)
    assert result["max_drawdown_pct"] == pytest.approx(-8.0)
    assert result["status"] == "침식관찰"


def test_calculate_nav_metrics_uses_only_distributions_inside_nav_period():
    nav = pd.DataFrame(
        {"NAV": [10000.0, 10100.0]},
        index=pd.to_datetime(["2026-02-01", "2026-03-01"]),
    )
    distributions = pd.DataFrame(
        {
            "지급기준일": pd.to_datetime(["2026-01-15", "2026-02-15", "2026-03-15"]),
            "주당분배금": [999.0, 100.0, 999.0],
        }
    )

    result = calculate_nav_metrics(nav, distributions)

    assert result["cumulative_distribution"] == 100.0
    assert result["status"] == "양호"


def test_calculate_nav_metrics_rejects_insufficient_nav_history():
    nav = pd.DataFrame({"NAV": [10000.0]}, index=pd.to_datetime(["2026-01-02"]))

    with pytest.raises(ValueError, match="2개 이상"):
        calculate_nav_metrics(nav, pd.DataFrame())


def test_fetch_nav_history_uses_local_cache_after_first_fetch(tmp_path):
    calls = []

    def fake_fetcher(start, end, ticker):
        calls.append((start, end, ticker))
        return pd.DataFrame(
            {"종가": [10010, 9910], "NAV": [10000.0, 9900.0], "괴리율": [0.1, 0.1]},
            index=pd.to_datetime(["2026-01-02", "2026-01-05"]),
        )

    first = fetch_nav_history(
        "152100", "20260101", "20260131", tmp_path, fetcher=fake_fetcher
    )
    second = fetch_nav_history(
        "152100", "20260101", "20260131", tmp_path, fetcher=fake_fetcher
    )

    assert len(calls) == 1
    assert list(first.columns) == ["종가", "NAV", "괴리율"]
    pd.testing.assert_frame_equal(first, second, check_freq=False)
    assert (tmp_path / "152100_20260101_20260131.csv").exists()


def test_fetch_nav_history_refetches_corrupt_cache(tmp_path):
    cache = tmp_path / "152100_20260101_20260131.csv"
    cache.write_text(
        "date,종가,NAV,괴리율\n2026-01-02,oops,-1,nan\n", encoding="utf-8"
    )
    calls = []

    def fake_fetcher(start, end, ticker):
        calls.append((start, end, ticker))
        return pd.DataFrame(
            {"종가": [10000, 10100], "NAV": [9990, 10090], "괴리율": [0.1, 0.1]},
            index=pd.to_datetime(["2026-01-02", "2026-01-05"]),
        )

    result = fetch_nav_history(
        "152100", "20260101", "20260131", tmp_path, fetcher=fake_fetcher
    )

    assert calls == [("20260101", "20260131", "152100")]
    assert list(result.columns) == ["종가", "NAV", "괴리율"]


@pytest.mark.parametrize(
    "bad_frame",
    [
        pd.DataFrame(
            {"종가": [10000, 10100], "NAV": [9990, 10090], "괴리율": [0.1, 0.1]},
            index=pd.DatetimeIndex([pd.NaT, pd.Timestamp("2026-01-05")]),
        ),
        pd.DataFrame(
            {"종가": [10000, 10100], "NAV": [9990, 10090], "괴리율": [0.1, 0.1]},
            index=pd.to_datetime(["2026-01-02", "2026-01-02"]),
        ),
        pd.DataFrame(
            {"종가": [10000, 10100], "NAV": [9990, 10090], "괴리율": [0.1, 0.1]},
            index=pd.to_datetime(["2025-12-31", "2026-01-02"]),
        ),
        pd.DataFrame(
            {"종가": [10000, float("inf")], "NAV": [9990, 10090], "괴리율": [0.1, 0.1]},
            index=pd.to_datetime(["2026-01-02", "2026-01-05"]),
        ),
        pd.DataFrame(
            {"종가": [10000, 10100], "NAV": [9990, -1], "괴리율": [0.1, 0.1]},
            index=pd.to_datetime(["2026-01-02", "2026-01-05"]),
        ),
    ],
)
def test_fetch_nav_history_rejects_invalid_new_response(tmp_path, bad_frame):
    with pytest.raises(ValueError, match="KRX NAV"):
        fetch_nav_history(
            "152100",
            "20260101",
            "20260131",
            tmp_path,
            fetcher=lambda *_: bad_frame,
        )


def test_load_distributions_for_isin_keeps_only_profit_distributions(tmp_path):
    raw = pd.DataFrame(
        {
            "ISIN": ["KR7152100004", "KR7152100004", "KR7000000000"],
            "지급기준일": ["20260131", "20260228", "20260131"],
            "주당분배금": ["0.12", "9.999", "0.5"],
            "결산과표기준가": ["120", "9,999", "500"],
            "배당구분": ["이익분배", "청산분배", "이익분배"],
        }
    )
    raw.to_csv(tmp_path / "seibro_분배금_20260228.csv", index=False, encoding="utf-8-sig")

    result = load_distributions_for_isin(tmp_path, "KR7152100004")

    assert len(result) == 1
    assert result.iloc[0]["주당분배금"] == 120
    assert result.iloc[0]["지급기준일"] == pd.Timestamp("2026-01-31")


def test_KRX코드가_이미_있어도_컬럼이_중복되지_않는다():
    """대시보드가 미리 붙여둔 KRX코드 위에 다시 호출해도 컬럼은 하나여야 한다."""
    frame = pd.DataFrame({"ISIN": ["KR7152100004"], "종목명": ["PLUS 200"],
                          "KRX코드": ["틀린값"]})
    out = filter_valid_isin_rows(frame)
    assert list(out.columns).count("KRX코드") == 1
    assert out["KRX코드"].iloc[0] == "152100"     # ISIN에서 다시 계산한 값으로 덮어쓴다
