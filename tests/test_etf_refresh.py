# -*- coding: utf-8 -*-
"""국내 ETF 공식 API 갱신 — 병합·수익률 계산은 표만 받는 순수 함수라 그대로 검증한다."""
from pathlib import Path
import sys

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import etf_refresh


def old_info() -> pd.DataFrame:
    """마지막 정보 CSV 흉내 — 정적 칸(총보수·자산분류)이 산 채로 있다."""
    return pd.DataFrame({
        "KRX코드": ["069500", "102110"],
        "종목명": ["KODEX 200", "TIGER 200"],
        "총보수": [0.15, 0.05],
        "자산분류": ["주식", "주식"],
        "기초지수명": ["코스피 200", ""],
        "종가": [30000.0, 29000.0],
        "NAV": [30010.0, 29010.0],
        "시가총액": [1e12, 9e11],
        "기준일자": ["20260805", "20260805"],
    })


def fresh_prices() -> pd.DataFrame:
    return pd.DataFrame({
        "KRX코드": ["069500", "102110", "999999"],       # 999999 = 신규 상장
        "종목명": ["KODEX 200", "TIGER 200", "신규 ETF"],
        "종가": [31000.0, 30000.0, 10000.0],
        "NAV": [31005.0, 30020.0, 10001.0],
        "시가총액": [1.1e12, 9.5e11, 5e10],
        "기초지수명": ["코스피 200", "코스피 200", "어떤지수"],
        "기준일자": "20260806",
    })


def test_시세만_바뀌고_정적_칸은_남는다():
    out = etf_refresh.merge_prices(old_info(), fresh_prices())
    row = out[out["KRX코드"] == "069500"].iloc[0]

    assert row["종가"] == 31000.0            # 시세는 오늘 값
    assert row["총보수"] == 0.15             # 총보수·분류는 어제 값 그대로
    assert row["자산분류"] == "주식"
    assert row["기준일자"] == "20260806"


def test_비어_있던_기초지수명은_API_값으로_채운다():
    out = etf_refresh.merge_prices(old_info(), fresh_prices())

    assert out[out["KRX코드"] == "102110"]["기초지수명"].iloc[0] == "코스피 200"


def test_신규_상장은_덧붙고_정적_칸은_빈다():
    out = etf_refresh.merge_prices(old_info(), fresh_prices())
    new = out[out["KRX코드"] == "999999"]

    assert len(new) == 1
    assert new["종가"].iloc[0] == 10000.0
    assert pd.isna(new["총보수"].iloc[0])     # 아직 모르는 값은 비워 둔다


def test_정보_CSV가_없으면_시세만으로_시작한다():
    out = etf_refresh.merge_prices(pd.DataFrame(), fresh_prices())

    assert len(out) == 3


def make_day(codes_prices: dict, bas_dd: str) -> pd.DataFrame:
    return pd.DataFrame({
        "KRX코드": list(codes_prices),
        "종가": list(codes_prices.values()),
        "기준일자": bas_dd,
    })


def test_수익률은_구간_시작_종가_대비다():
    now = make_day({"069500": 33000.0}, "20260806")
    ytd = make_day({"069500": 30000.0}, "20260102")
    year = make_day({"069500": 22000.0}, "20250806")

    out = etf_refresh.compute_returns(now, ytd, year, {
        "기준일자": "20260806", "시작일_YTD": "20260102", "시작일_1년": "20250806"})

    assert out["YTD수익률"].iloc[0] == 10.0
    assert out["1년수익률"].iloc[0] == 50.0


def test_그_사이_상장된_종목은_해당_수익률만_빈다():
    now = make_day({"069500": 33000.0, "신규": 10000.0}, "20260806")
    ytd = make_day({"069500": 30000.0, "신규": 8000.0}, "20260102")
    year = make_day({"069500": 22000.0}, "20250806")     # 1년 전엔 없었다

    out = etf_refresh.compute_returns(now, ytd, year, {
        "기준일자": "20260806", "시작일_YTD": "20260102", "시작일_1년": "20250806"})
    new = out[out["KRX코드"] == "신규"].iloc[0]

    assert new["YTD수익률"] == 25.0
    assert pd.isna(new["1년수익률"])


def test_두_수익률이_다_빈_종목은_표에서_뺀다():
    now = make_day({"069500": 33000.0, "고아": 5000.0}, "20260806")
    ytd = make_day({"069500": 30000.0}, "20260102")
    year = make_day({"069500": 22000.0}, "20250806")

    out = etf_refresh.compute_returns(now, ytd, year, {
        "기준일자": "20260806", "시작일_YTD": "20260102", "시작일_1년": "20250806"})

    assert "고아" not in set(out["KRX코드"])
