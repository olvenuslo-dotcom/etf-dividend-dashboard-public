# -*- coding: utf-8 -*-
"""연3회 주기 — 두 해 연속 같은 3개 달에만 지급하면 불규칙이 아니라 주기다.

37단계가 남긴 것: 국내 지수형(예: 4·7·10월)은 횟수로는 분기, 간격으로는 반기가
나와 늘 '주기 확인'으로 샜다. 이름 추측이 아니라 **지급 달 기록**으로만 판정한다.
"""
from datetime import date
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import calendar_ics
import classify
import portfolio


# ── 판정 (classify) ─────────────────────────────────────
def _history(dates):
    """분류 원본 CSV 모양의 최소 이력."""
    return pd.DataFrame([{
        "ISIN": "KR7000000001", "종목명": "테스트 지수형", "운용사": "테스트자산운용",
        "유형": "국내 주식형", "지급기준일": d, "실지급일": d,
        "주당분배금": "100", "분배율": "0.5", "배당구분": "이익분배",
    } for d in dates])


TRI_DATES = ["20241031", "20250430", "20250731",   # 앞 해: 4·7·10월
             "20251031", "20260430", "20260731"]   # 뒷 해: 4·7·10월


def test_두_해_연속_같은_3개_달이면_연3회다():
    got = classify.classify(_history(TRI_DATES)).iloc[0]
    assert got["분배주기"] == classify.TRI_CYCLE
    assert got["지급월목록"] == "4,7,10"
    # 판정이 끝났으므로 '주기 확인'을 달지 않는다
    assert got["비고"] == ""


def test_한_해만_봐서는_연3회로_판정하지_않는다():
    """분기(4회)에서 한 회 빠진 것과 구분이 안 되므로 앞 해 확인 없이는 안 바꾼다."""
    got = classify.classify(_history(TRI_DATES[3:])).iloc[0]
    assert got["분배주기"] != classify.TRI_CYCLE
    assert pd.isna(got["지급월목록"]) or not got["지급월목록"]


def test_두_해의_달이_다르면_연3회가_아니다():
    dates = ["20241130", "20250228", "20250630",   # 앞 해: 2·6·11월
             "20251031", "20260430", "20260731"]   # 뒷 해: 4·7·10월
    got = classify.classify(_history(dates)).iloc[0]
    assert got["분배주기"] != classify.TRI_CYCLE


def test_지급이_4회면_연3회가_아니다():
    dates = ["20241031", "20250131", "20250430", "20250731",
             "20251031", "20260131", "20260430", "20260731"]
    got = classify.classify(_history(dates)).iloc[0]
    assert got["분배주기"] == "분기"


# ── 예측 (calendar_ics.add_cycle) ───────────────────────
def test_연3회는_지급월목록의_달로_예측한다():
    anchor = pd.Timestamp("2026-07-31")
    months = [4, 7, 10]
    got = [calendar_ics.add_cycle(anchor, "연3회", k, months) for k in (1, 2, 3)]
    # 3·3·6개월 간격이 그대로 나와야 한다 (고정 걸음이면 달이 밀린다)
    assert got == [pd.Timestamp("2026-10-31"),
                   pd.Timestamp("2027-04-30"),
                   pd.Timestamp("2027-07-31")]


def test_연3회_월말이_아니면_같은_일을_유지한다():
    got = calendar_ics.add_cycle(pd.Timestamp("2026-11-15"), "연3회", 1, [4, 7, 10])
    assert got == pd.Timestamp("2027-04-15")


def test_달_목록이_없으면_예측하지_않는다():
    """틀린 달로 이벤트를 만드느니 안 만든다."""
    assert calendar_ics.add_cycle(pd.Timestamp("2026-07-31"), "연3회", 1, None) is None


def test_지급월목록_파싱():
    assert calendar_ics.parse_pay_months("4,7,10") == [4, 7, 10]
    assert calendar_ics.parse_pay_months("") is None
    assert calendar_ics.parse_pay_months(None) is None
    assert calendar_ics.parse_pay_months(float("nan")) is None
    assert calendar_ics.parse_pay_months("0,7") is None      # 달이 아니면 버린다


# ── 현금흐름 (portfolio.monthly_cashflow) ───────────────
def test_현금흐름은_지급월에만_놓인다():
    evaluated = pd.DataFrame([{
        "분배주기": "연3회", "지급월목록": "4,7,10",
        "최근_주당분배금": 100.0, "수량": 10,
        "계좌유형": "일반", "최근_지급기준일": "2026-07-31",
    }])
    flow = portfolio.monthly_cashflow(evaluated, months=12, today=date(2026, 8, 14))
    # 직전 지급(7월)은 지났으니 10월 → 다음 해 4월 → 7월
    assert list(flow["월"]) == ["2026-10", "2027-04", "2027-07"]
    assert (flow["세전"] == 1000.0).all()
