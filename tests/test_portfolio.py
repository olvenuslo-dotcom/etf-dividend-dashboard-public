# -*- coding: utf-8 -*-
"""포트폴리오 계산 테스트: 평가손익 / 분배금 손익 / 매수일 유무 / 비중."""
from datetime import date
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import portfolio


def _etf_table() -> pd.DataFrame:
    return pd.DataFrame({
        "종목명": ["테스트 배당ETF"],
        "ISIN": ["KR7152100004"],          # → KRX코드 152100
        "총보수": [0.5],
        "최근_주당분배금": [100.0],
        "투자지역": ["미국"],
        "테마섹터": ["배당주"],
    })


def _holding(**overrides) -> pd.DataFrame:
    base = {"계좌": ["연금"], "종목명": ["테스트 배당ETF"], "KRX코드": [""],
            "수량": [10.0], "매수단가": [1000.0], "매수일": [date(2026, 1, 1)],
            "메모": [""]}
    base.update({k: [v] for k, v in overrides.items()})
    return pd.DataFrame(base)


def _patch_sources(monkeypatch, 종가=1200.0, 분배=None):
    """KRX 시세와 세이브로 분배 이력을 가짜로 갈아끼운다."""
    info = pd.DataFrame({"KRX코드": ["152100"], "ISIN": ["KR7152100004"],
                         "종가": [종가], "NAV": [1190.0], "기준일자": ["20260731"]})
    monkeypatch.setattr(portfolio.etf_info, "load_latest", lambda: info)
    if 분배 is None:
        분배 = pd.DataFrame({
            "ISIN": ["KR7152100004"] * 3,
            "지급기준일": pd.to_datetime(["2025-10-31", "2026-03-31", "2026-06-30"]),
            "주당분배금": [50.0, 30.0, 20.0],
        })
    monkeypatch.setattr(portfolio, "load_distributions", lambda: 분배)


def test_평가금액과_평가손익(monkeypatch):
    _patch_sources(monkeypatch)
    ev = portfolio.evaluate(_holding(), _etf_table(), today=date(2026, 8, 2))
    row = ev.iloc[0]
    assert row["매수원금"] == 10_000        # 10주 × 1,000원
    assert row["평가금액"] == 12_000        # 10주 × 1,200원
    assert row["평가손익"] == 2_000
    assert round(row["평가수익률"], 2) == 20.0


def test_매수일_이후_분배금만_센다(monkeypatch):
    _patch_sources(monkeypatch)
    ev = portfolio.evaluate(_holding(매수일=date(2026, 1, 1)), _etf_table(),
                            today=date(2026, 8, 2))
    # 2026-01-01 이후 = 3/31(30) + 6/30(20) = 50원 × 10주
    assert ev.iloc[0]["받은분배금"] == 500
    assert ev.iloc[0]["기준"] == "매수일 이후"


def test_매수일이_없으면_최근_12개월(monkeypatch):
    _patch_sources(monkeypatch)
    ev = portfolio.evaluate(_holding(매수일=None), _etf_table(), today=date(2026, 8, 2))
    # 최근 12개월 = 2025-10-31(50) + 30 + 20 = 100원 × 10주
    assert ev.iloc[0]["받은분배금"] == 1_000
    assert "매수일 미입력" in ev.iloc[0]["기준"]


def test_총손익은_평가손익과_분배금의_합(monkeypatch):
    _patch_sources(monkeypatch)
    ev = portfolio.evaluate(_holding(), _etf_table(), today=date(2026, 8, 2))
    row = ev.iloc[0]
    assert row["총손익"] == row["평가손익"] + row["받은분배금"]
    assert round(row["총수익률"], 2) == 25.0     # (2000 + 500) / 10000


def test_매수가대비분배율은_내_원가_기준(monkeypatch):
    _patch_sources(monkeypatch)
    ev = portfolio.evaluate(_holding(), _etf_table(), today=date(2026, 8, 2))
    row = ev.iloc[0]
    # 최근 12개월 주당분배금 100원 ÷ 매수단가 1,000원 = 10%
    assert round(row["매수가대비분배율"], 2) == 10.0
    # 현재가 1,200원 기준이면 8.33% — 둘이 달라야 의미가 있다
    assert round(row["현재가대비분배율"], 2) == 8.33


def test_KRX코드가_비면_종목명으로_찾는다(monkeypatch):
    _patch_sources(monkeypatch)
    ev = portfolio.evaluate(_holding(KRX코드=""), _etf_table(), today=date(2026, 8, 2))
    assert ev.iloc[0]["KRX코드"] == "152100"


def test_현재가를_못_찾아도_죽지_않는다(monkeypatch):
    monkeypatch.setattr(portfolio.etf_info, "load_latest", lambda: pd.DataFrame())
    monkeypatch.setattr(portfolio, "load_distributions",
                        lambda: pd.DataFrame(columns=["ISIN", "지급기준일", "주당분배금"]))
    ev = portfolio.evaluate(_holding(), _etf_table(), today=date(2026, 8, 2))
    assert pd.isna(ev.iloc[0]["현재가"])
    assert pd.isna(ev.iloc[0]["평가금액"])


def test_합계와_비중(monkeypatch):
    _patch_sources(monkeypatch)
    two = pd.concat([_holding(), _holding(계좌="ISA", 수량=30.0)], ignore_index=True)
    ev = portfolio.evaluate(two, _etf_table(), today=date(2026, 8, 2))
    assert round(ev["비중"].sum(), 1) == 100.0
    total = portfolio.summarize(ev)
    assert total["종목수"] == 2
    assert total["계좌수"] == 2
    assert total["평가금액"] == 12_000 + 36_000


def test_지역별_비중표(monkeypatch):
    _patch_sources(monkeypatch)
    ev = portfolio.evaluate(_holding(), _etf_table(), today=date(2026, 8, 2))
    part = portfolio.breakdown(ev, "투자지역")
    assert list(part["투자지역"]) == ["미국"]
    assert round(part["비중"].iloc[0], 1) == 100.0


def test_빈_보유는_빈_결과():
    assert portfolio.evaluate(portfolio.empty_holdings(), _etf_table()).empty
    assert portfolio.summarize(pd.DataFrame()) == {}


def test_저장하고_다시_읽기(tmp_path):
    path = tmp_path / "보유종목.csv"
    portfolio.save_holdings(_holding(), path)
    back = portfolio.load_holdings(path)
    assert list(back.columns) == portfolio.HOLDING_COLUMNS
    assert back["수량"].iloc[0] == 10.0
    assert back["매수일"].iloc[0] == date(2026, 1, 1)


def test_파일이_없으면_빈_표(tmp_path):
    assert portfolio.load_holdings(tmp_path / "없음.csv").empty
