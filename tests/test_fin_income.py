# -*- coding: utf-8 -*-
"""금융소득종합과세 — 연 2,000만원 선까지 얼마나 남았나.

여기서 지키려는 것은 셋이다.
1) **연금·ISA 를 세면 안 된다.** 과세이연·분리과세라 종합소득에 안 들어간다.
   세어 버리면 멀쩡한 사람에게 '넘었다'고 겁을 준다.
2) **세전으로 잰다.** 세후로 재면 실제보다 낮게 나와 안심하게 된다.
3) **우리 자료에 없는 금융소득**(예금 이자 등)을 0 으로 두지 않고 넣을 자리를 준다.
"""
from datetime import date
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import fin_income


def _row(**kw):
    base = {"계좌": "테스트", "계좌유형": "일반(위탁)", "ISIN": "KR7152100004",
            "종목명": "테스트ETF", "수량": 100, "매수일": pd.NaT,
            "분배주기": "월", "최근_주당분배금": 100.0,
            "최근_지급기준일": pd.Timestamp("2026-07-31")}
    base.update(kw)
    return base


def _dist(dates, per_share=100.0, isin="KR7152100004"):
    return pd.DataFrame({
        "ISIN": [isin] * len(dates),
        "지급기준일": [pd.Timestamp(d) for d in dates],
        "주당분배금": [per_share] * len(dates),
    })


# ── 계좌유형 (핵심) ──────────────────────────────────────
def test_일반계좌는_합산한다():
    assert fin_income.is_combined("일반(위탁)")


def test_연금과_ISA는_합산하지_않는다():
    """과세이연·분리과세라 종합소득에 안 들어간다."""
    assert not fin_income.is_combined("연금저축")
    assert not fin_income.is_combined("퇴직연금(IRP/DC)")
    assert not fin_income.is_combined("ISA")


def test_계좌유형이_비면_일반으로_본다():
    """portfolio 와 같은 규칙 — 세금을 과소평가하지 않는 쪽으로."""
    assert fin_income.is_combined("")
    assert fin_income.is_combined(None)


def test_연금계좌_분배금은_올해_소득에_안_들어간다(monkeypatch):
    monkeypatch.setattr(fin_income.portfolio, "load_distributions",
                        lambda: _dist(["2026-03-31", "2026-06-30"]))
    일반 = pd.DataFrame([_row(계좌유형="일반(위탁)")])
    연금 = pd.DataFrame([_row(계좌유형="연금저축")])
    today = date(2026, 8, 13)
    assert fin_income.received_this_year(일반, today) == 20_000    # 100원 × 100주 × 2회
    assert fin_income.received_this_year(연금, today) == 0.0


# ── 올해 받은 것 ─────────────────────────────────────────
def test_작년_분배금은_안_센다(monkeypatch):
    monkeypatch.setattr(fin_income.portfolio, "load_distributions",
                        lambda: _dist(["2025-12-31", "2026-01-31"]))
    frame = pd.DataFrame([_row()])
    assert fin_income.received_this_year(frame, date(2026, 8, 13)) == 10_000


def test_아직_안_온_분배금은_받은_것에_안_넣는다(monkeypatch):
    monkeypatch.setattr(fin_income.portfolio, "load_distributions",
                        lambda: _dist(["2026-03-31", "2026-11-30"]))
    frame = pd.DataFrame([_row()])
    assert fin_income.received_this_year(frame, date(2026, 8, 13)) == 10_000


def test_매수일_이전_분배는_내_소득이_아니다(monkeypatch):
    monkeypatch.setattr(fin_income.portfolio, "load_distributions",
                        lambda: _dist(["2026-02-28", "2026-06-30"]))
    frame = pd.DataFrame([_row(매수일=pd.Timestamp("2026-05-01"))])
    assert fin_income.received_this_year(frame, date(2026, 8, 13)) == 10_000


# ── 한계세율 ────────────────────────────────────────────
def test_한계세율_구간():
    assert fin_income.marginal_rate(10_000_000) == 0.06
    assert fin_income.marginal_rate(30_000_000) == 0.15
    assert fin_income.marginal_rate(60_000_000) == 0.24
    assert fin_income.marginal_rate(2_000_000_000) == 0.45


# ── 요약 ────────────────────────────────────────────────
def _no_dist(monkeypatch):
    monkeypatch.setattr(fin_income.portfolio, "load_distributions",
                        lambda: pd.DataFrame(columns=["ISIN", "지급기준일",
                                                      "주당분배금"]))


def test_기준을_안_넘으면_초과가_0이다(monkeypatch):
    _no_dist(monkeypatch)
    got = fin_income.summary(pd.DataFrame(), other_income=5_000_000,
                             today=date(2026, 8, 13))
    assert got["연간_합계"] == 5_000_000
    assert got["초과액"] == 0
    assert got["넘었나"] is False
    assert got["남은_여유"] == 15_000_000
    assert got["추가세금_어림"] == 0


def test_기준을_넘으면_초과분만_센다(monkeypatch):
    _no_dist(monkeypatch)
    got = fin_income.summary(pd.DataFrame(), other_income=25_000_000,
                             today=date(2026, 8, 13))
    assert got["초과액"] == 5_000_000
    assert got["넘었나"] is True
    assert got["남은_여유"] == 0


def test_추가세금은_이미_낸_14퍼센트를_빼고_센다(monkeypatch):
    """원천징수로 이미 낸 소득세 14% 는 공제된다. 지방소득세 10% 는 얹는다."""
    _no_dist(monkeypatch)
    got = fin_income.summary(pd.DataFrame(), other_income=25_000_000,
                             other_taxable_base=60_000_000,
                             today=date(2026, 8, 13))
    # 과세표준 6,000만 + 초과 500만 → 24% 구간
    assert got["한계세율"] == 0.24
    assert round(got["추가세금_어림"]) == round(5_000_000 * 0.10 * 1.1)


def test_한계세율이_14퍼센트_아래면_더_낼_것이_없다(monkeypatch):
    """6% 구간이면 종합과세가 오히려 유리하다 — 음수 세금을 만들지 않는다."""
    _no_dist(monkeypatch)
    got = fin_income.summary(pd.DataFrame(), other_income=25_000_000,
                             other_taxable_base=0, today=date(2026, 8, 13))
    assert got["한계세율"] == 0.06
    assert got["추가세금_어림"] == 0


def test_다른_금융소득도_합계에_들어간다(monkeypatch):
    """예금 이자·타 증권사 배당은 우리 자료에 없다. 0 으로 두면 과소평가한다."""
    monkeypatch.setattr(fin_income.portfolio, "load_distributions",
                        lambda: _dist(["2026-03-31"]))
    frame = pd.DataFrame([_row()])
    없이 = fin_income.summary(frame, today=date(2026, 8, 13))
    있게 = fin_income.summary(frame, other_income=1_000_000,
                              today=date(2026, 8, 13))
    assert 있게["연간_합계"] - 없이["연간_합계"] == 1_000_000


# ── 계좌별 ──────────────────────────────────────────────
def test_계좌별로_합산여부를_보여준다(monkeypatch):
    """왜 안 세어졌는지가 보여야 한다."""
    monkeypatch.setattr(fin_income.portfolio, "load_distributions",
                        lambda: _dist(["2026-03-31"]))
    frame = pd.DataFrame([
        _row(계좌="일반계좌", 계좌유형="일반(위탁)"),
        _row(계좌="내IRP", 계좌유형="퇴직연금(IRP/DC)"),
    ])
    table = fin_income.by_account(frame, date(2026, 8, 13))
    보임 = dict(zip(table["계좌"], table["합산대상"]))
    assert 보임["일반계좌"] is True
    assert 보임["내IRP"] is False
    # 합산 대상이 아니어도 '얼마 받았는지'는 보여 준다
    assert table.set_index("계좌").loc["내IRP", "올해_받은"] == 10_000


# ── 빈 입력 ─────────────────────────────────────────────
def test_보유가_없어도_안_죽는다(monkeypatch):
    _no_dist(monkeypatch)
    assert fin_income.received_this_year(pd.DataFrame()) == 0.0
    assert fin_income.expected_rest_of_year(pd.DataFrame()) == 0.0
    assert fin_income.by_account(pd.DataFrame()).empty
