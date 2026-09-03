# -*- coding: utf-8 -*-
"""수급 캐시 — 가짜 fetcher 만 쓰고 네트워크를 타지 않는다."""
from pathlib import Path
import sys

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from flow import fetch


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch, "CACHE_DIR", tmp_path / "flow_cache")
    monkeypatch.setattr(fetch, "WATCHLIST_CSV", tmp_path / "관심종목.csv")
    monkeypatch.setattr(fetch, "sessions_back",
                        lambda days: ["20260805", "20260806"][-days:])
    return tmp_path


def fake_day(bas_dd: str, shares: float = 1000.0) -> pd.DataFrame:
    return pd.DataFrame({
        "KRX코드": ["069500", "102110"],
        "종목명": ["KODEX 200", "TIGER 200"],
        "상장주식수": [shares, 2000.0],
        "거래대금": [1e9, 2e9],
        "종가": [30000.0, 29000.0],
        "NAV": [30010.0, 29010.0],
    })


def test_받은_날은_다시_요청하지_않는다(cache):
    calls = []

    def fetcher(bas_dd):
        calls.append(bas_dd)
        return fake_day(bas_dd)

    assert fetch.ensure_days(2, fetcher=fetcher) == 2
    assert fetch.ensure_days(2, fetcher=fetcher) == 0        # 전부 캐시

    assert calls == ["20260805", "20260806"]                 # 한 번씩만


def test_빈_응답은_파일을_만들지_않아_다음에_다시_시도한다(cache):
    calls = []

    def empty_then_data(bas_dd):
        calls.append(bas_dd)
        return pd.DataFrame() if len(calls) <= 2 else fake_day(bas_dd)

    assert fetch.ensure_days(2, fetcher=empty_then_data) == 0    # 둘 다 미배포
    assert fetch.ensure_days(2, fetcher=empty_then_data) == 2    # 이번엔 받아진다


def test_조회가_죽어도_앱은_안_죽고_그_날만_건너뛴다(cache):
    def boom(bas_dd):
        if bas_dd == "20260805":
            raise RuntimeError("서버 오류")
        return fake_day(bas_dd)

    assert fetch.ensure_days(2, fetcher=boom) == 1           # 산 날만 저장


def test_이력은_한_종목만_뽑아_날짜순으로_준다(cache):
    fetch.ensure_days(2, fetcher=lambda d: fake_day(d, shares=1000.0
                                                    if d == "20260805" else 1100.0))

    out = fetch.shares_history("069500", days=60)

    assert len(out) == 2                                     # 있는 만큼만 (N일치)
    assert list(out["상장주식수"]) == [1000.0, 1100.0]       # 과거 → 최근
    assert out.index.is_monotonic_increasing


def test_캐시에_없는_종목은_빈_표다(cache):
    fetch.ensure_days(2, fetcher=fake_day)

    assert fetch.shares_history("없는코드", days=60).empty


def test_관심종목은_저장하고_다시_읽는다(cache):
    frame = pd.DataFrame({"종목코드": ["069500", "069500", "102110"],
                          "종목명": ["A", "A", "B"]})
    fetch.save_watchlist(frame)

    out = fetch.load_watchlist()

    assert list(out["종목코드"]) == ["069500", "102110"]     # 중복 제거
