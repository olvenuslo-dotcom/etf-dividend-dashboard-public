# -*- coding: utf-8 -*-
"""PRD 10절 시나리오. 가짜 표만 쓰고 네트워크를 타지 않는다."""
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from flow import metrics, signals


def days(n: int) -> pd.DatetimeIndex:
    """거래일처럼 쓸 날짜 index (주말 여부는 계산에 영향이 없다)."""
    return pd.date_range("2026-01-01", periods=n, freq="D")


def make_trades(n: int, 기관: float, 외국인: float, 개인: float = 0.0) -> pd.DataFrame:
    """하루치 값이 일정한 순매수 표."""
    return pd.DataFrame(
        {"기관합계": [기관] * n, "외국인": [외국인] * n, "개인": [개인] * n},
        index=days(n))


def make_shares(values: list[float], turnover: float = 1e9) -> pd.DataFrame:
    return pd.DataFrame(
        {"상장주식수": values, "거래대금": [turnover] * len(values)},
        index=days(len(values)))


# ── 🔴 이탈 (PRD v1.1 — 상장주식수 기반) ───────────────────
def test_20일60일_모두_주식수_감소면_이탈이_켜진다():
    shares = make_shares([1000 - i for i in range(60)])      # 계속 감소

    summary = metrics.summarize(pd.DataFrame(), shares, 60)

    assert signals.outflow(summary) is True
    assert signals.OUTFLOW in signals.flags_for(summary)


def test_20일만_감소하고_60일은_증가면_이탈이_꺼진다():
    """오경보 방지 — PRD 가 20일·60일을 모두 요구하는 이유다."""
    # 앞 40일 크게 늘고 뒤 20일 조금 줄었다 → 20일 -, 60일 +
    values = [1000 + i * 10 for i in range(40)] + [1390 - i for i in range(20)]
    shares = make_shares(values)

    summary = metrics.summarize(pd.DataFrame(), shares, 60)

    assert summary["상장주식수_증감_20일"] < 0
    assert summary["상장주식수_증감_60일"] > 0
    assert signals.outflow(summary) is False


def test_주식수가_줄지_않으면_이탈이_꺼진다():
    shares = make_shares([1000] * 60)                        # 변화 없음

    assert signals.outflow(metrics.summarize(pd.DataFrame(), shares, 60)) is False


# ── 🟢 유입 (PRD v1.1) ─────────────────────────────────────
def test_5일_연속_증가에_20일도_증가면_유입이_켜진다():
    shares = make_shares([1000] * 15 + [1010, 1020, 1030, 1040, 1050])

    summary = metrics.summarize(pd.DataFrame(), shares, 20)

    assert summary["연속증가일"] >= 5
    assert signals.inflow(summary) is True


def test_4일만_연속_증가면_유입이_꺼진다():
    # 마지막 4일만 증가 (그 앞은 같은 값이라 연속이 끊긴다)
    shares = make_shares([1000] * 16 + [1010, 1020, 1030, 1040])

    summary = metrics.summarize(pd.DataFrame(), shares, 20)

    assert summary["연속증가일"] == 4
    assert signals.inflow(summary) is False


def test_연속_증가여도_20일_합계가_줄었으면_유입이_꺼진다():
    # 급락 후 5일 반등: 연속증가일 5 지만 20일 합계는 마이너스
    shares = make_shares([1500] * 4 + [1200] * 10
                         + [1000, 1010, 1020, 1030, 1040, 1050])

    summary = metrics.summarize(pd.DataFrame(), shares, 20)

    assert summary["연속증가일"] >= 5
    assert summary["상장주식수_증감_20일"] < 0
    assert signals.inflow(summary) is False


# ── ⚠ 분배율 함정 ──────────────────────────────────────────
def test_분배율_상위30퍼센트에_이탈이면_함정이_켜진다():
    표 = pd.DataFrame({
        "종목코드": ["A", "B", "C", "D"],
        "연환산 분배율": [30.0, 5.0, 4.0, 3.0],      # A 가 상위 30%
        "이탈": [True, True, False, False],
    })

    out = signals.flag_distribution_trap(표)

    assert list(out["함정"]) == [True, False, False, False]


def test_분배율이_높아도_이탈이_아니면_함정이_아니다():
    표 = pd.DataFrame({
        "종목코드": ["A", "B", "C", "D"],
        "연환산 분배율": [30.0, 5.0, 4.0, 3.0],
        "이탈": [False, False, False, False],
    })

    assert not signals.flag_distribution_trap(표)["함정"].any()


def test_분배율이_비면_함정을_판정하지_않는다():
    """PRD: 미연결 시 판정하지 않는다 (거짓 안심을 주지 않으려고)."""
    표 = pd.DataFrame({
        "종목코드": ["A", "B"],
        "연환산 분배율": [None, None],
        "이탈": [True, True],
    })

    out = signals.flag_distribution_trap(표)

    assert list(out["함정"]) == [False, False]


def test_비교대상이_하나뿐이면_상위30퍼센트를_따지지_않는다():
    # 자기 혼자면 분위수가 자기 자신이라 무조건 상위가 된다 → 판정하지 않는다
    표 = pd.DataFrame({"종목코드": ["A"], "연환산 분배율": [30.0], "이탈": [True]})

    assert list(signals.flag_distribution_trap(표)["함정"]) == [False]


# ── 집계 자체 ──────────────────────────────────────────────
def test_기관합계가_없으면_세부항목을_더한다():
    trades = pd.DataFrame(
        {"금융투자": [1e8] * 20, "투신": [2e8] * 20, "연기금": [3e8] * 20},
        index=days(20))

    assert metrics.net_buy(trades, 20, "기관합계") == 20 * 6e8


def test_상장기간이_조회기간보다_짧으면_있는_만큼만_센다():
    trades = make_trades(10, 기관=1e8, 외국인=0)
    shares = make_shares([1000] * 10)

    summary = metrics.summarize(trades, shares, 60)

    assert summary["데이터일수"] == 10
    assert summary["60일_기관"] == 10 * 1e8      # 60일을 요구해도 10일치만 있다


def test_빈_표를_줘도_죽지_않는다():
    summary = metrics.summarize(pd.DataFrame(), pd.DataFrame(), 60)

    assert summary["20일_스마트머니"] == 0.0
    assert signals.flags_for(summary) == []
