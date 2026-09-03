# -*- coding: utf-8 -*-
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import classify

RATE = 1400.0        # 원/달러 — 테스트를 읽기 쉽게 딱 떨어지는 값으로


def risk(values, markets, rate=RATE):
    return list(classify.liquidation_risk(
        pd.Series(values, dtype="float64"), pd.Series(markets), rate))


def test_국내는_50억_미만이_상폐요건이다():
    # 순자산총액 50억원 미만은 실제 상장폐지 요건선이다
    assert risk([49e8, 50e8], ["한국", "한국"]) == [classify.DELIST_LEVEL,
                                                    classify.WATCH_LEVEL]


def test_국내는_100억_넘으면_표시가_없다():
    assert risk([100e8, 500e8], ["한국", "한국"]) == ["", ""]


def test_미국은_5천만달러_미만이_위험이다():
    # 규정이 아니라 운용사가 접는 관행선 → '위험'
    assert risk([49e6 * RATE, 51e6 * RATE], ["미국", "미국"]) == [
        classify.RISK_LEVEL, classify.WATCH_LEVEL]


def test_미국은_1억달러_넘으면_표시가_없다():
    assert risk([100e6 * RATE, 800e6 * RATE], ["미국", "미국"]) == ["", ""]


def test_미국_100만달러_미만은_상폐요건이다():
    # 거래소 상장유지 요건($1M)선 아래는 관행선보다 급하다
    assert risk([0.5e6 * RATE], ["미국"]) == [classify.DELIST_LEVEL]


def test_같은_금액도_시장에_따라_다르게_본다():
    # 300억원: 국내에선 넉넉하지만 미국($21M)에선 청산 후보다
    assert risk([300e8, 300e8], ["한국", "미국"]) == ["", classify.RISK_LEVEL]


def test_시총이_비면_판정하지_않는다():
    assert risk([None], ["한국"]) == [""]


def test_환율이_없으면_미국은_판정하지_않는다():
    # 달러 기준을 원화로 못 바꾸면 잘못 표시하느니 비워 둔다
    out = classify.liquidation_risk(
        pd.Series([1e8, 1e8]), pd.Series(["한국", "미국"]), rate=None)

    assert list(out) == [classify.DELIST_LEVEL, ""]


def test_상장_1년_안_된_국내는_상폐요건에서_뺀다():
    # 국내 요건은 '상장 1년 초과'부터 걸린다 → 갓 상장한 소형 ETF는 주의로 낮춘다
    today = pd.Timestamp.today()
    out = classify.liquidation_risk(
        pd.Series([30e8, 30e8]), pd.Series(["한국", "한국"]), RATE,
        listed_on=pd.Series([today - pd.Timedelta(days=100),     # 신생
                             today - pd.Timedelta(days=800)]))   # 1년 넘음

    assert list(out) == [classify.WATCH_LEVEL, classify.DELIST_LEVEL]


def test_상장일이_없으면_예전처럼_판정한다():
    out = classify.liquidation_risk(
        pd.Series([30e8]), pd.Series(["한국"]), RATE,
        listed_on=pd.Series([None]))

    assert list(out) == [classify.DELIST_LEVEL]
