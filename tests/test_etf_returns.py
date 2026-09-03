# -*- coding: utf-8 -*-
"""ETF 수익률 수집·결합 테스트: 구간 경계 / 결측 처리 / 일정 표 결합."""
from datetime import date
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import etf_returns
from dashboard import _split_description


def test_구간_경계는_모두_개장일():
    end, ytd_start, year_start = etf_returns.trading_bounds(date(2026, 8, 2))
    # 2026-08-02는 일요일 → 마지막 개장일 7/31(금)
    assert end == "20260731"
    # 2026-01-01은 신정 휴장 → 올해 첫 개장일로 앞당김
    assert ytd_start == "20260102"
    # 1년 전(2025-07-31)은 목요일 개장일
    assert year_start == "20250731"


def test_연초가_개장일이면_그대로():
    # 2025-01-02(목)이 첫 개장일. 1/1은 항상 휴장이므로 1/2 이후여야 한다
    _, ytd_start, _ = etf_returns.trading_bounds(date(2025, 6, 30))
    assert ytd_start >= "20250102"


def test_휴장일에_돌려도_기준일은_직전_개장일():
    # 2026-01-01 신정에 실행해도 기준일이 미래로 가지 않는다
    end, _, _ = etf_returns.trading_bounds(date(2026, 1, 1))
    assert end <= "20260101"


def test_시작가_0인_종목은_제외(monkeypatch):
    """구간에 거래가 없어 시가가 0이면 등락률이 의미 없으므로 버린다."""
    fake = pd.DataFrame(
        {"시가": [1000, 0], "등락률": [5.0, 0.0]},
        index=pd.Index(["069500", "0000A0"], name="티커"),
    )

    class FakeStock:
        @staticmethod
        def get_etf_price_change_by_ticker(fromdate, todate):
            return fake

    # 파싱을 보는 테스트라 KDM 가드를 열어 준다 (조회는 가짜로 대체).
    # 가드 자체는 test_kdm_guard.py 가 지킨다.
    monkeypatch.setenv("ETF_ALLOW_KDM", "1")
    monkeypatch.setitem(sys.modules, "pykrx", type("M", (), {"stock": FakeStock}))
    monkeypatch.setattr("pykrx.stock", FakeStock, raising=False)
    out = etf_returns.fetch_price_change("20260102", "20260731")
    assert list(out["KRX코드"]) == ["069500"]


def test_수익률_파일이_없으면_빈_표(tmp_path, monkeypatch):
    monkeypatch.setattr(etf_returns, "RAW_DIR", tmp_path)
    assert etf_returns.load_latest().empty


def test_저장본을_다시_읽으면_숫자형(tmp_path, monkeypatch):
    monkeypatch.setattr(etf_returns, "RAW_DIR", tmp_path)
    pd.DataFrame({
        "KRX코드": ["069500"], "YTD수익률": [3.21], "1년수익률": [-1.5],
        "시작일_YTD": ["20260102"], "시작일_1년": ["20250731"], "기준일자": ["20260731"],
    }).to_csv(tmp_path / "krx_etf수익률_20260802.csv", index=False, encoding="utf-8-sig")
    frame = etf_returns.load_latest()
    assert frame["YTD수익률"].dtype.kind == "f"
    assert frame["KRX코드"].iloc[0] == "069500"     # 앞자리 0이 살아 있어야 한다


def test_일정_설명을_짧은_칸으로_쪼갠다():
    desc = ("주기: 월 | 직전 분배율: 0.21% | 예상 기준일: 26-8-31 "
            "| 예상 지급일: 26-9-1 | ※ 예상치(실제는 공시로 확정)")
    out = _split_description(desc)
    assert out["주기"] == "월"
    assert out["분배율"] == 0.21
    assert out["예상기준일"] == "26-8-31"


def test_설명이_비어도_죽지_않는다():
    out = _split_description("")
    assert out["주기"] == ""
    assert pd.isna(out["분배율"])
