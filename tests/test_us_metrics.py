# -*- coding: utf-8 -*-
"""미국 ETF 파생 지표(TTM 분배율·수익률·성장률)와 발행사 자료 파싱 테스트."""
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import us_etf
import us_issuers


def _frame(dividends: dict, close_start=100.0, close_end=110.0) -> pd.DataFrame:
    """2년치 일별 종가 + 배당. 종가는 시작→끝으로 곧게 오른다."""
    days = pd.date_range("2024-08-06", "2026-08-05", freq="B")
    prices = pd.Series(
        [close_start + (close_end - close_start) * i / (len(days) - 1)
         for i in range(len(days))], index=days)
    frame = pd.DataFrame({"Close": prices, "Volume": 1_000.0, "Dividends": 0.0})
    for day, amount in dividends.items():
        frame.loc[pd.Timestamp(day), "Dividends"] = amount
    return frame


def test_연간분배율은_최근_12개월_합으로_낸다():
    # 최근 12개월 4회 × 1.0 = 4.0, 그 앞 12개월 4회 × 0.5 = 2.0
    out = us_etf.summarize_one(_frame({
        "2024-09-30": 0.5, "2024-12-31": 0.5, "2025-03-31": 0.5, "2025-06-30": 0.5,
        "2025-09-30": 1.0, "2025-12-31": 1.0, "2026-03-31": 1.0, "2026-06-30": 1.0,
    }))
    assert out["지급횟수_12개월"] == 4          # 최근 12개월만 센다
    assert out["연간주당분배금"] == 4.0
    assert out["연간분배율"] == 3.64            # 4.0 ÷ 110 × 100
    assert out["분배금성장률"] == 100.0         # 2.0 → 4.0


def test_직전_1회분과_연간분배율은_다르다():
    out = us_etf.summarize_one(_frame({
        "2025-09-30": 1.0, "2025-12-31": 1.0, "2026-03-31": 1.0, "2026-06-30": 1.0}))
    # 주기가 다른 종목을 비교할 때 헷갈리는 자리 — 1회분 0.91% vs 연간 3.64%
    assert out["최근_분배율"] == 0.9091
    assert out["연간분배율"] == 3.64


def test_수익률은_연초와_1년전_기준으로_각각_낸다():
    out = us_etf.summarize_one(_frame({}, close_start=100.0, close_end=110.0))
    assert out["1년수익률"] is not None and out["1년수익률"] > 0
    assert out["YTD수익률"] is not None and out["YTD수익률"] > 0
    # 2년에 걸쳐 10% 올랐으니 1년 수익률은 그보다 작다
    assert out["1년수익률"] < 10


def test_배당이_한해뿐이면_성장률은_안_낸다():
    out = us_etf.summarize_one(_frame({"2026-06-30": 1.0}))
    assert out["분배금성장률"] is None
    assert out["연간분배율"] is not None


def test_변동성과_최대낙폭이_나온다():
    out = us_etf.summarize_one(_frame({}))
    assert out["변동성"] is not None
    assert out["최대낙폭"] <= 0                 # 낙폭은 0 이하


def test_SPDR_퍼센트와_금액을_숫자로_바꾼다():
    assert us_issuers._spdr_percent(pd.Series(["0.10%", "-", "7.04%"])).tolist()[0] == 0.10
    assert pd.isna(us_issuers._spdr_percent(pd.Series(["-"])).iloc[0])
    money = us_issuers._spdr_money(pd.Series(["$27,846.31 M", "$1.5 B", "-"]))
    assert money.iloc[0] == 27_846_310_000.0
    assert money.iloc[1] == 1_500_000_000.0
    assert pd.isna(money.iloc[2])


def test_iShares_값은_r_키에서_꺼낸다():
    assert us_issuers._num({"d": "0.03", "r": 0.03}) == 0.03
    assert us_issuers._num(0.05) == 0.05
    assert pd.isna(us_issuers._num(None))
