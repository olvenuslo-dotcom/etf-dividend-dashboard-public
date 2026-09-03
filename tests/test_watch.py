# -*- coding: utf-8 -*-
"""관심종목 현금흐름 시뮬레이션(watch.py) 테스트 — 네트워크 없이 계산만."""
from datetime import date
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import etf_info
import us_etf
import watch


def _etf_table() -> pd.DataFrame:
    """국내 월배당 1 + 미국 분기배당 1 + 미분배 1 이 든 목록."""
    return pd.DataFrame({
        "종목명": ["국내월배당ETF", "US Dividend ETF", "신규상장ETF"],
        "KRX코드": ["152100", "SCHD", "999999"],
        "상장시장": ["한국", "미국", "한국"],
        "분배주기": ["월", "분기", "미분배"],
        "지급횟수_12개월": [12, 4, 0],
        "연간주당분배금": [1200.0, 1440.0, None],   # 월 100원 / 분기 360원
        "최근_지급기준일": [date(2026, 7, 31), date(2026, 6, 24), None],
        "1년수익률": [5.0, 8.0, None],
        "분배금성장률": [10.0, 12.0, None],
    })


def _watchlist(**overrides) -> pd.DataFrame:
    base = {"종목코드": ["152100"], "종목명": ["국내월배당ETF"],
            "수량": [10.0], "투자금액": [None], "메모": [""]}
    base.update(overrides)
    return pd.DataFrame(base)


def _patch_prices(monkeypatch, kr=10_000.0, us=40_000.0):
    monkeypatch.setattr(etf_info, "load_latest", lambda: pd.DataFrame({
        "KRX코드": ["152100", "999999"], "종가": [kr, 5_000.0]}))
    monkeypatch.setattr(us_etf, "load_latest", lambda: pd.DataFrame({
        "티커": ["SCHD"], "종가": [us], "기준일자": ["20260806"], "환율": [1400.0]}))


def test_루틴_판정_칸이_따라온다(monkeypatch):
    """관심종목 탭이 5분 루틴 필터(routine.apply_filters)를 걸려면 이 칸들이 있어야 한다."""
    _patch_prices(monkeypatch)
    table = _etf_table()
    table["루틴점수"] = [2, -1, None]
    table["루틴_가격잠식"] = [1, -1, None]
    table["커버드콜"] = [False, True, False]
    out = watch.plan(_watchlist(), table)
    row = out.iloc[0]
    assert row["루틴점수"] == 2
    assert row["루틴_가격잠식"] == 1
    assert row["커버드콜"] == False    # noqa: E712 - 값 자체를 확인


def test_수량기준_연분배금과_분배율(monkeypatch):
    _patch_prices(monkeypatch)
    out = watch.plan(_watchlist(), _etf_table())
    row = out.iloc[0]
    assert row["예상연분배금"] == 12_000.0          # 1,200원 × 10주
    assert row["투자금액"] == 100_000.0             # 10주 × 10,000원
    assert row["예상연분배율"] == 12.0
    # 일반(위탁) 15.4% 원천징수
    assert round(row["예상연분배금_세후"], 0) == round(12_000 * 0.846, 0)


def test_투자금액만_넣으면_정수_주수로_바꾼다(monkeypatch):
    _patch_prices(monkeypatch)
    out = watch.plan(_watchlist(수량=[None], 투자금액=[105_000.0]), _etf_table())
    row = out.iloc[0]
    assert row["수량"] == 10.0                      # 105,000 ÷ 10,000 → 10주 (내림)
    assert row["투자금액"] == 100_000.0             # 실제 투자금액으로 다시 잡는다


def test_미국_종목은_계좌와_무관하게_15퍼센트(monkeypatch):
    _patch_prices(monkeypatch)
    wl = _watchlist(종목코드=["SCHD"], 종목명=["US Dividend ETF"])
    out = watch.plan(wl, _etf_table(), account_type="연금저축")
    row = out.iloc[0]
    assert round(row["예상연분배금_세후"], 0) == round(row["예상연분배금"] * 0.85, 0)
    assert "매수 불가" in row["비고"]                # 연금저축으로는 미국 상장 못 산다


def test_미분배는_비고에_적힌다(monkeypatch):
    _patch_prices(monkeypatch)
    wl = _watchlist(종목코드=["999999"], 종목명=["신규상장ETF"])
    out = watch.plan(wl, _etf_table())
    assert "미분배" in out.iloc[0]["비고"]


def test_목록에_없는_종목도_죽지_않는다(monkeypatch):
    _patch_prices(monkeypatch)
    wl = _watchlist(종목코드=["000000"], 종목명=["없는종목"])
    out = watch.plan(wl, _etf_table())
    assert out.iloc[0]["비고"] == "목록에 없음"


def test_일정은_월배당이면_12회_나온다(monkeypatch):
    _patch_prices(monkeypatch)
    planned = watch.plan(_watchlist(), _etf_table())
    out = watch.schedule(planned, _etf_table(), months=12,
                         today=date(2026, 8, 8))
    assert 11 <= len(out) <= 13                     # 월배당 1년치
    assert (out["세전"] == 1_000.0).all()           # (1,200 ÷ 12회) × 10주
    # 매수마감은 기준일보다 앞이다
    assert (pd.to_datetime(out["예상매수마감"]) < pd.to_datetime(out["예상기준일"])).all()


def test_mark_는_코드로_관심_칸을_붙인다():
    df = pd.DataFrame({"KRX코드": ["152100", "069500"], "종목명": ["A", "B"]})
    wl = _watchlist()
    out = watch.mark(df, wl)
    assert list(out["관심"]) == [True, False]


def test_mark_는_파일이_없으면_전부_False(tmp_path, monkeypatch):
    monkeypatch.setattr(watch, "WATCH_CSV", tmp_path / "없음.csv")
    df = pd.DataFrame({"KRX코드": ["152100"], "종목명": ["A"]})
    assert not watch.mark(df)["관심"].any()
