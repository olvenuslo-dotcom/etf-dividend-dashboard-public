# -*- coding: utf-8 -*-
"""포트폴리오의 미국 상장 종목 처리 — 현재가·분배금·세금·계좌 제약."""
from datetime import date
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import portfolio
import us_etf


def _etf_table() -> pd.DataFrame:
    """국내 1종목 + 미국 1종목이 든 ETF 목록."""
    return pd.DataFrame({
        "종목명": ["국내배당ETF", "SCHD Schwab U.S. Dividend Equity ETF"],
        "종목코드": ["152100", "SCHD"],
        "ISIN": ["KR7152100004", ""],
        "상장시장": ["한국", "미국"],
        "총보수": [0.5, 0.06],
        "최근_주당분배금": [100.0, 360.0],
        "투자지역": ["한국", "미국"],
        "테마섹터": ["배당주", "배당주"],
    })


def _holdings(계좌유형="일반(위탁)") -> pd.DataFrame:
    return pd.DataFrame({
        "계좌": ["일반", "일반"],
        "계좌유형": ["일반(위탁)", 계좌유형],
        "종목명": ["국내배당ETF", "SCHD Schwab U.S. Dividend Equity ETF"],
        "KRX코드": ["152100", "SCHD"],
        "수량": [10.0, 5.0],
        "매수단가": [1000.0, 40_000.0],
        "매수일": [date(2026, 1, 1), date(2026, 1, 1)],
        "메모": ["", ""],
    })


def _patch(monkeypatch, 미국종가=42_000.0):
    """KRX 시세·세이브로 분배금·미국 수집분을 가짜로 갈아끼운다."""
    info = pd.DataFrame({"KRX코드": ["152100"], "ISIN": ["KR7152100004"],
                         "종가": [1200.0], "NAV": [1190.0], "기준일자": ["20260805"]})
    monkeypatch.setattr(portfolio.etf_info, "load_latest", lambda: info)
    monkeypatch.setattr(portfolio, "load_distributions", lambda: pd.DataFrame({
        "ISIN": ["KR7152100004"],
        "지급기준일": pd.to_datetime(["2026-03-31"]),
        "주당분배금": [50.0],
    }))
    monkeypatch.setattr(us_etf, "load_latest", lambda: pd.DataFrame({
        "티커": ["SCHD"], "종가": [미국종가], "기준일자": ["20260805"],
        "환율": [1400.0],
    }))
    monkeypatch.setattr(us_etf, "load_dividends", lambda: pd.DataFrame({
        "티커": ["SCHD", "SCHD"],
        "배당락일": pd.to_datetime(["2026-03-25", "2026-06-24"]),
        "주당분배금": [350.0, 360.0],       # 원화 환산된 값
    }))


def test_미국_종목도_현재가가_붙는다(monkeypatch):
    _patch(monkeypatch)
    ev = portfolio.evaluate(_holdings(), _etf_table())
    us = ev[ev["KRX코드"] == "SCHD"].iloc[0]
    assert us["현재가"] == 42_000.0
    assert us["평가금액"] == 210_000.0          # 5주 × 42,000원
    assert us["평가손익"] == 10_000.0


def test_미국_배당이력으로_받은분배금을_센다(monkeypatch):
    _patch(monkeypatch)
    ev = portfolio.evaluate(_holdings(), _etf_table())
    us = ev[ev["KRX코드"] == "SCHD"].iloc[0]
    # 매수일(2026-01-01) 이후 배당 2회 × 5주
    assert us["받은분배금"] == (350.0 + 360.0) * 5


def test_미국_분배금은_계좌와_무관하게_15퍼센트를_뗀다(monkeypatch):
    _patch(monkeypatch)
    ev = portfolio.evaluate(_holdings(), _etf_table())
    us = ev[ev["KRX코드"] == "SCHD"].iloc[0]
    kr = ev[ev["KRX코드"] == "152100"].iloc[0]
    assert round(us["원천징수세율"], 2) == 15.0
    assert us["받은분배금_세후"] == us["받은분배금"] * 0.85
    # 국내 일반계좌는 15.4% 그대로
    assert round(kr["원천징수세율"], 2) == 15.4


def test_연금계좌에_미국종목이_있으면_잡아낸다(monkeypatch):
    _patch(monkeypatch)
    ev = portfolio.evaluate(_holdings(계좌유형="연금저축"), _etf_table())
    conflicts = portfolio.account_conflicts(ev)
    assert len(conflicts) == 1
    assert conflicts.iloc[0]["KRX코드"] == "SCHD"


def test_정상_계좌면_경고가_없다(monkeypatch):
    _patch(monkeypatch)
    ev = portfolio.evaluate(_holdings(), _etf_table())
    assert portfolio.account_conflicts(ev).empty


def test_미국_수집분이_없으면_현재가만_빈다(monkeypatch):
    _patch(monkeypatch)
    monkeypatch.setattr(us_etf, "load_latest", lambda: pd.DataFrame(
        columns=us_etf.OUTPUT_COLUMNS))
    ev = portfolio.evaluate(_holdings(), _etf_table())
    us = ev[ev["KRX코드"] == "SCHD"].iloc[0]
    assert pd.isna(us["현재가"])
    # 국내 종목은 그대로 계산된다
    kr = ev[ev["KRX코드"] == "152100"].iloc[0]
    assert kr["평가금액"] == 12_000.0
