# -*- coding: utf-8 -*-
"""미국 직접 상장 ETF 수집·결합 테스트 (네트워크 없이 계산 부분만)."""
from datetime import date
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import classify
import us_etf


def _price_frame(dividends: dict) -> pd.DataFrame:
    """1년치 종가·거래량에 배당을 얹은 가짜 야후 응답."""
    days = pd.date_range("2025-08-06", "2026-08-05", freq="B")
    frame = pd.DataFrame({
        "Close": 100.0,
        "Volume": 1_000.0,
        "Dividends": 0.0,
    }, index=days)
    for day, amount in dividends.items():
        frame.loc[pd.Timestamp(day), "Dividends"] = amount
    return frame


def test_배당_요약은_횟수와_간격을_뽑는다():
    frame = _price_frame({"2026-05-29": 0.5, "2026-06-30": 0.5, "2026-07-31": 0.6})
    out = us_etf.summarize_one(frame)
    assert out["지급횟수_12개월"] == 3
    assert out["최근_주당분배금"] == 0.6
    assert out["최근_지급기준일"] == date(2026, 7, 31)
    assert out["중앙값간격_일"] == 31.5          # 32일·31일의 중앙값
    assert out["최근_분배율"] == 0.6             # 0.6 ÷ 100 × 100(%)


def test_배당이_없으면_분배칸이_빈다():
    out = us_etf.summarize_one(_price_frame({}))
    assert out["지급횟수_12개월"] == 0
    assert out["최근_주당분배금"] is None and out["최근_지급기준일"] is None


def test_시세가_없으면_건너뛴다():
    assert us_etf.summarize_one(pd.DataFrame()) is None
    assert us_etf.summarize_one(None) is None


def _us_row(**overrides) -> pd.DataFrame:
    base = {
        "티커": "SCHD", "종목명": "Schwab U.S. Dividend Equity ETF",
        "거래소": "NYSE/AMEX", "운용사": "Schwab", "통화": "USD",
        "시가총액": 1.0e14, "총보수": 0.06, "종가": 40_000.0,
        "지급횟수_12개월": 4, "중앙값간격_일": 91.0,
        "최근_주당분배금": 360.0, "최근_분배율": 0.9,
        "최근_지급기준일": date(2026, 6, 24), "일평균거래대금": 5.0e8,
        "카테고리": "Large Value", "환율": 1400.0, "기준일자": "20260805",
    }
    base.update(overrides)
    return pd.DataFrame([base])


def _kr_result() -> pd.DataFrame:
    row = {c: None for c in classify.OUTPUT_COLUMNS}
    row.update({"종목명": "국내배당ETF", "분배주기": "월", "상장시장": classify.KR_MARKET,
                "ISIN": "KR7000001001"})
    return pd.DataFrame([row], columns=classify.OUTPUT_COLUMNS)


def test_미국_종목은_상장시장과_과세유형이_따로다():
    out = classify.add_us_etfs(_kr_result(), _us_row())
    us = out[out["상장시장"] == classify.US_MARKET].iloc[0]
    # 티커는 종목명이 아니라 '종목코드' 칸에 둔다 (표에서 종목명 옆 칸)
    assert us["종목명"] == "Schwab U.S. Dividend Equity ETF"
    assert us["종목코드"] == "SCHD"
    assert us["과세유형"] == classify.US_TAX_TYPE
    assert us["ISIN"] == ""                        # KRX NAV 조회 대상이 아니다
    assert us["분배주기"] == "분기"                 # 12개월 4회


def test_매수마감은_배당락_1영업일_전이다():
    # 야후 날짜는 배당락일(ex-date)이라 국내(T-2)와 규칙이 다르다
    out = classify.add_us_etfs(_kr_result(), _us_row(최근_지급기준일=date(2026, 6, 24)))
    us = out[out["상장시장"] == classify.US_MARKET].iloc[0]
    assert us["최근_매수마감일"] == date(2026, 6, 23)   # 화요일 → 월요일


def test_거래대금이_적으면_안_들어온다():
    out = classify.add_us_etfs(_kr_result(), _us_row(일평균거래대금=5_000.0),
                               min_turnover=1_000_000.0)
    assert (out["상장시장"] == classify.US_MARKET).sum() == 0


def test_옵션인컴은_커버드콜로_분류된다():
    out = classify.add_us_etfs(
        _kr_result(),
        _us_row(티커="JEPI", 종목명="JPMorgan Equity Premium Income ETF"))
    us = out[out["상장시장"] == classify.US_MARKET].iloc[0]
    assert us["전략"] == "커버드콜"


def test_운용사가_비어도_nan_글자가_안_새어나온다():
    out = classify.add_us_etfs(_kr_result(), _us_row(운용사=float("nan")))
    us = out[out["상장시장"] == classify.US_MARKET].iloc[0]
    assert us["운용사"] == ""


def test_채권_ETF는_자산분류가_채권이다():
    out = classify.add_us_etfs(
        _kr_result(),
        _us_row(티커="AGG", 종목명="iShares Core U.S. Aggregate Bond ETF",
                카테고리="Intermediate Core Bond"))
    us = out[out["상장시장"] == classify.US_MARKET].iloc[0]
    assert us["자산분류"] == "채권"


def test_미국_수집분이_없으면_국내만_남는다():
    out = classify.add_us_etfs(_kr_result(), pd.DataFrame())
    assert len(out) == 1 and out["상장시장"].iloc[0] == classify.KR_MARKET
