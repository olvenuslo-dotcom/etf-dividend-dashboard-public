# -*- coding: utf-8 -*-
"""8단계 기능 테스트: 계좌유형 과세 / 시세 새로고침 / 현금흐름 / 리밸런싱 / 실현손익."""
from datetime import date
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import portfolio
from test_portfolio import _etf_table, _holding, _patch_sources


# ── 계좌유형별 과세 ────────────────────────────────────────
def test_일반계좌는_바로_원천징수():
    assert portfolio.account_tax_rate("일반(위탁)") == 0.154
    assert portfolio.account_tax_rate("일반(위탁)", final=True) == 0.154


def test_연금과_ISA는_받을_때_안_뗀다():
    for kind in ("연금저축", "퇴직연금(IRP/DC)", "ISA"):
        assert portfolio.account_tax_rate(kind) == 0.0, kind
    # 연금은 나중에 내고, ISA는 한도 안이면 안 낸다
    assert portfolio.account_tax_rate("연금저축", final=True) == 0.055
    assert portfolio.account_tax_rate("ISA", final=True) == 0.0


def test_계좌유형이_비면_일반계좌로_본다():
    # 모르면 '떼는 쪽'으로 — 세금을 과소평가하지 않는다
    assert portfolio.account_tax_rate("") == 0.154
    assert portfolio.account_tax_rate("듣보계좌") == 0.154


def test_계좌유형에_따라_세후_분배금이_다르다(monkeypatch):
    _patch_sources(monkeypatch)
    일반 = portfolio.evaluate(_holding(계좌유형="일반(위탁)"), _etf_table(),
                             today=date(2026, 8, 2)).iloc[0]
    연금 = portfolio.evaluate(_holding(계좌유형="연금저축"), _etf_table(),
                             today=date(2026, 8, 2)).iloc[0]
    assert 일반["받은분배금"] == 연금["받은분배금"]                # 세전은 같고
    assert 일반["받은분배금_세후"] < 연금["받은분배금_세후"]        # 세후는 연금이 많다
    assert round(일반["받은분배금_세후"], 2) == round(500 * (1 - 0.154), 2)
    assert 연금["받은분배금_세후"] == 500
    # 절세효과 = 일반 계좌였다면 뗐을 금액
    assert round(연금["절세효과"], 2) == round(500 * 0.154, 2)
    assert 일반["절세효과"] == 0.0


# ── 시세 새로고침 ──────────────────────────────────────────
def test_새_시세로_평가를_다시_계산(monkeypatch):
    _patch_sources(monkeypatch, 종가=1200.0)
    ev = portfolio.evaluate(_holding(), _etf_table(), today=date(2026, 8, 2))
    assert ev.iloc[0]["평가금액"] == 12_000

    새시세 = pd.DataFrame({"ISIN": ["KR7152100004"], "종가": [1500.0], "NAV": [1490.0]})
    row = portfolio.apply_live_prices(ev, 새시세).iloc[0]
    assert row["현재가"] == 1500.0
    assert row["평가금액"] == 15_000
    assert row["평가손익"] == 5_000
    assert round(row["평가수익률"], 2) == 50.0
    assert row["총손익"] == 5_000 + row["받은분배금"]      # 분배금은 그대로 살아 있어야


def test_새_시세에_없는_종목은_기존값_유지(monkeypatch):
    _patch_sources(monkeypatch, 종가=1200.0)
    ev = portfolio.evaluate(_holding(), _etf_table(), today=date(2026, 8, 2))
    빈시세 = pd.DataFrame({"ISIN": ["KR7000000000"], "종가": [9999.0], "NAV": [9999.0]})
    assert portfolio.apply_live_prices(ev, 빈시세).iloc[0]["현재가"] == 1200.0


# ── 월별 현금흐름 ──────────────────────────────────────────
def _with_cycle(monkeypatch, cycle: str, last: str, **holding):
    _patch_sources(monkeypatch)
    table = _etf_table()
    table["분배주기"] = [cycle]
    table["최근_지급기준일"] = [pd.Timestamp(last)]
    return portfolio.evaluate(_holding(**holding), table, today=date(2026, 8, 2))


def test_월배당은_매달_한_줄씩(monkeypatch):
    ev = _with_cycle(monkeypatch, "월", "2026-07-31")
    flow = portfolio.monthly_cashflow(ev, months=6, today=date(2026, 8, 2))
    assert list(flow["월"]) == ["2026-08", "2026-09", "2026-10",
                               "2026-11", "2026-12", "2027-01"]
    assert (flow["세전"] == 100.0 * 10).all()          # 주당 100원 × 10주


def test_분기배당은_세_달에_한_번(monkeypatch):
    ev = _with_cycle(monkeypatch, "분기", "2026-06-30")
    flow = portfolio.monthly_cashflow(ev, months=12, today=date(2026, 8, 2))
    assert list(flow["월"]) == ["2026-09", "2026-12", "2027-03", "2027-06"]


def test_일반계좌면_현금흐름도_세후가_적다(monkeypatch):
    ev = _with_cycle(monkeypatch, "월", "2026-07-31", 계좌유형="일반(위탁)")
    flow = portfolio.monthly_cashflow(ev, months=3, today=date(2026, 8, 2))
    assert (flow["세후"] < flow["세전"]).all()


def test_연금계좌면_세전과_세후가_같다(monkeypatch):
    ev = _with_cycle(monkeypatch, "월", "2026-07-31", 계좌유형="연금저축")
    flow = portfolio.monthly_cashflow(ev, months=3, today=date(2026, 8, 2))
    assert (flow["세후"] == flow["세전"]).all()


def test_분배주기를_모르면_현금흐름에서_뺀다(monkeypatch):
    _patch_sources(monkeypatch)
    ev = portfolio.evaluate(_holding(), _etf_table(), today=date(2026, 8, 2))
    assert portfolio.monthly_cashflow(ev, months=12).empty


def test_실제_수령액을_달별로(monkeypatch):
    _patch_sources(monkeypatch)
    ev = portfolio.evaluate(_holding(매수일=date(2026, 1, 1)), _etf_table(),
                            today=date(2026, 8, 2))
    past = portfolio.received_by_month(ev, months=12, today=date(2026, 8, 2))
    assert list(past["월"]) == ["2026-03", "2026-06"]
    assert list(past["받은금액"]) == [300.0, 200.0]      # 30원×10주, 20원×10주


# ── 리밸런싱 ──────────────────────────────────────────────
def _two_accounts(monkeypatch):
    _patch_sources(monkeypatch)
    two = pd.concat([_holding(계좌="연금"), _holding(계좌="ISA", 수량=30.0)],
                    ignore_index=True)
    return portfolio.evaluate(two, _etf_table(), today=date(2026, 8, 2))


def test_오차_안이면_유지(monkeypatch):
    ev = _two_accounts(monkeypatch)
    targets = pd.DataFrame({"구분": ["테스트 배당ETF"], "목표비중": [100.0]})
    assert list(portfolio.rebalance(ev, targets, column="종목명")["판정"]) == ["유지"]


def test_목표보다_적으면_더_사기(monkeypatch):
    ev = _two_accounts(monkeypatch)
    # 연금 12,000 / ISA 36,000 → 25% / 75%
    targets = pd.DataFrame({"구분": ["연금", "ISA"], "목표비중": [50.0, 50.0]})
    plan = portfolio.rebalance(ev, targets, column="계좌", band=5.0).set_index("구분")
    assert plan.loc["연금", "판정"] == "더 사기"
    assert plan.loc["ISA", "판정"] == "덜기"
    # 총 48,000원의 25%p = 12,000원을 옮기면 맞는다
    assert round(plan.loc["연금", "조정금액"]) == 12_000
    assert round(plan.loc["ISA", "조정금액"]) == -12_000


def test_허용오차를_넓히면_유지로_바뀐다(monkeypatch):
    ev = _two_accounts(monkeypatch)
    targets = pd.DataFrame({"구분": ["연금", "ISA"], "목표비중": [50.0, 50.0]})
    plan = portfolio.rebalance(ev, targets, column="계좌", band=30.0)
    assert set(plan["판정"]) == {"유지"}


def test_조정금액의_합은_0(monkeypatch):
    ev = _two_accounts(monkeypatch)
    targets = pd.DataFrame({"구분": ["연금", "ISA"], "목표비중": [30.0, 70.0]})
    assert round(portfolio.rebalance(ev, targets, column="계좌")["조정금액"].sum(), 6) == 0.0


def test_목표가_없으면_빈_표(monkeypatch):
    assert portfolio.rebalance(_two_accounts(monkeypatch), pd.DataFrame()).empty


# ── 실현손익 (이동평균법) ──────────────────────────────────
def _trades(rows) -> pd.DataFrame:
    frame = pd.DataFrame(rows, columns=["계좌", "종목명", "KRX코드", "일자",
                                        "구분", "수량", "단가", "수수료"])
    frame["일자"] = pd.to_datetime(frame["일자"])
    return frame


def test_이동평균법으로_실현손익():
    row = portfolio.realized_pnl(_trades([
        ["일반", "A", "", "2026-01-05", "매수", 10, 1000, 0],
        ["일반", "A", "", "2026-03-05", "매수", 10, 1400, 0],
        ["일반", "A", "", "2026-06-05", "매도", 15, 1600, 100],
    ])).iloc[0]
    assert row["평균매수단가"] == 1200.0              # (10×1000 + 10×1400) / 20
    assert row["실현손익"] == (1600 - 1200) * 15 - 100
    assert round(row["수익률"], 2) == 32.78


def test_매수_수수료도_평균단가에_들어간다():
    row = portfolio.realized_pnl(_trades([
        ["일반", "A", "", "2026-01-05", "매수", 10, 1000, 500],
        ["일반", "A", "", "2026-06-05", "매도", 10, 1000, 0],
    ])).iloc[0]
    # 매수 수수료 500원 때문에 평단이 1,050원 → 같은 값에 팔면 500원 손실
    assert row["평균매수단가"] == 1050.0
    assert row["실현손익"] == -500.0


def test_계좌가_다르면_평균단가도_따로():
    by_account = portfolio.realized_pnl(_trades([
        ["일반", "A", "", "2026-01-05", "매수", 10, 1000, 0],
        ["ISA", "A", "", "2026-02-05", "매수", 10, 2000, 0],
        ["일반", "A", "", "2026-06-05", "매도", 10, 1500, 0],
        ["ISA", "A", "", "2026-06-05", "매도", 10, 1500, 0],
    ])).set_index("계좌")["실현손익"]
    assert by_account["일반"] == 5_000        # 1,000 → 1,500
    assert by_account["ISA"] == -5_000        # 2,000 → 1,500


def test_보유량보다_많이_팔면_보유량까지만():
    result = portfolio.realized_pnl(_trades([
        ["일반", "A", "", "2026-01-05", "매수", 10, 1000, 0],
        ["일반", "A", "", "2026-06-05", "매도", 50, 1200, 0],
    ]))
    assert result.iloc[0]["수량"] == 10


def test_매수만_있으면_실현손익_없음():
    assert portfolio.realized_pnl(_trades([
        ["일반", "A", "", "2026-01-05", "매수", 10, 1000, 0],
    ])).empty


def test_매도가_먼저_와도_죽지_않는다():
    # 이전 계좌에서 옮겨온 물량 등 매수 기록이 없는 매도
    assert portfolio.realized_pnl(_trades([
        ["일반", "A", "", "2026-06-05", "매도", 10, 1200, 0],
    ])).empty


# ── 저장/불러오기 ─────────────────────────────────────────
def test_매매내역_저장하고_다시_읽기(tmp_path):
    path = tmp_path / "매매내역.csv"
    portfolio.save_trades(
        _trades([["일반", "A", "069500", "2026-01-05", "매수", 10, 1000, 50]]), path)
    back = portfolio.load_trades(path)
    assert list(back.columns) == portfolio.TRADE_COLUMNS
    assert back["KRX코드"].iloc[0] == "069500"      # 앞자리 0이 살아 있어야
    assert back["수수료"].iloc[0] == 50.0


def test_목표비중_저장하고_다시_읽기(tmp_path):
    path = tmp_path / "목표비중.csv"
    portfolio.save_targets(
        pd.DataFrame({"구분": ["미국", "한국"], "목표비중": [60.0, 40.0]}), path)
    back = portfolio.load_targets(path)
    assert list(back["구분"]) == ["미국", "한국"]
    assert back["목표비중"].sum() == 100.0


def test_없는_파일은_빈_표(tmp_path):
    assert portfolio.load_trades(tmp_path / "없음.csv").empty
    assert portfolio.load_targets(tmp_path / "없음.csv").empty


def test_계좌유형_없는_옛_CSV도_읽힌다(tmp_path):
    path = tmp_path / "보유종목.csv"
    pd.DataFrame({"계좌": ["일반"], "종목명": ["A"], "KRX코드": ["069500"],
                  "수량": [10], "매수단가": [1000], "매수일": ["2026-01-01"],
                  "메모": [""]}).to_csv(path, index=False, encoding="utf-8-sig")
    back = portfolio.load_holdings(path)
    assert back["계좌유형"].iloc[0] == portfolio.DEFAULT_ACCOUNT_TYPE
