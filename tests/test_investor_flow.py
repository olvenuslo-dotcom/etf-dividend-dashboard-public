# -*- coding: utf-8 -*-
"""투자자별 거래 (KIS 백엔드) — 페이지 백필과 기간 합계.

KIS [0416]은 한 번에 최근 30거래일만 준다. 긴 기간은 base_date 를 뒤로 밀며
받아야 하는데, 여기서 지키려는 것은 셋이다.
1) 구간을 덮을 때까지만 페이지를 넘긴다 (상장 초기에 닿으면 멈춘다)
2) 페이지 경계의 중복 날짜를 두 번 세지 않는다
3) 합계는 요청 구간 밖 날짜를 섞지 않는다
"""
from pathlib import Path
import sys

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import investor_flow


def _page(dates, sell=100.0, buy=150.0):
    """kis_api.investor_detail 흉내 — 날짜마다 금융투자·개인 두 그룹."""
    n = len(dates)
    return pd.DataFrame({
        "날짜": [pd.Timestamp(d) for d in dates],
        "종가": [1000.0] * n,
        "금융투자_매도량": [sell] * n,
        "금융투자_매수량": [buy] * n,
        "금융투자_순매수량": [buy - sell] * n,
        "개인_매도량": [sell * 2] * n,
        "개인_매수량": [buy * 2] * n,
        "개인_순매수량": [(buy - sell) * 2] * n,
    })


@pytest.fixture
def cache_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(investor_flow, "CACHE_DIR", tmp_path / "inv")
    return tmp_path / "inv"


def test_기간_합계가_투자자별로_나온다(monkeypatch, cache_dir):
    days = pd.bdate_range("2026-07-01", "2026-08-14")
    monkeypatch.setattr(investor_flow.kis_api, "investor_detail",
                        lambda code, base, use_cache=True: _page(days))
    got = investor_flow.fetch("069500", "20260701", "20260814")
    row = got.set_index("투자자").loc["금융투자"]
    assert row["매도"] == 100.0 * len(days)
    assert row["매수"] == 150.0 * len(days)
    assert row["순매수"] == 50.0 * len(days)
    assert "개인" in set(got["투자자"])


def test_긴_기간은_페이지를_뒤로_밀며_받는다(monkeypatch, cache_dir):
    """1페이지 = 30거래일. 두 달 구간이면 최소 두 번은 불러야 한다."""
    calls = []

    def fake(code, base, use_cache=True):
        calls.append(base)
        end = pd.Timestamp(base)
        days = pd.bdate_range(end=end, periods=30)
        return _page(days)

    monkeypatch.setattr(investor_flow.kis_api, "investor_detail", fake)
    monkeypatch.setattr(investor_flow.time, "sleep", lambda s: None)
    got = investor_flow.fetch("069500", "20260601", "20260814")
    assert len(calls) >= 2                       # 백필이 실제로 일어났다
    row = got.set_index("투자자").loc["금융투자"]
    # 6/1~8/14 영업일 수만큼만 합산됐다 (중복·구간 밖 없음)
    expected_days = len(pd.bdate_range("2026-06-01", "2026-08-14"))
    assert row["매도"] == 100.0 * expected_days


def test_페이지_경계_중복_날짜는_한_번만_센다(monkeypatch, cache_dir):
    """실제 페이지는 30행이다. 두 번째 페이지가 첫 페이지의 마지막 날을
    다시 담아 와도(경계 중복) 그 날을 두 번 세면 안 된다."""
    page1 = _page(pd.bdate_range(end="2026-08-14", periods=30))   # ~7/6 까지
    page2 = _page(pd.bdate_range(end=page1["날짜"].min(), periods=30))  # 7/6 겹침
    pages = [page1, page2]
    monkeypatch.setattr(investor_flow.kis_api, "investor_detail",
                        lambda code, base, use_cache=True: pages.pop(0))
    monkeypatch.setattr(investor_flow.time, "sleep", lambda s: None)
    start = page2["날짜"].min().strftime("%Y%m%d")
    got = investor_flow.fetch("069500", start, "20260814")
    expected_days = len(pd.bdate_range(page2["날짜"].min(), "2026-08-14"))
    assert got.set_index("투자자").loc["개인", "매도"] == 200.0 * expected_days


def test_상장_초기에_닿으면_더_안_부른다(monkeypatch, cache_dir):
    """짧은 페이지(<25행)가 오면 그 앞은 자료가 없다는 뜻이다."""
    calls = []

    def fake(code, base, use_cache=True):
        calls.append(base)
        return _page(pd.bdate_range("2026-08-01", "2026-08-14"))   # 10행뿐

    monkeypatch.setattr(investor_flow.kis_api, "investor_detail", fake)
    investor_flow.fetch("069500", "20250101", "20260814")
    assert len(calls) == 1


def test_기간_캐시가_두번째_호출을_막는다(monkeypatch, cache_dir):
    days = pd.bdate_range("2026-08-01", "2026-08-14")
    calls = []

    def fake(code, base, use_cache=True):
        calls.append(base)
        return _page(days)

    monkeypatch.setattr(investor_flow.kis_api, "investor_detail", fake)
    investor_flow.fetch("069500", "20260801", "20260814")
    investor_flow.fetch("069500", "20260801", "20260814")
    assert len(calls) == 1


def test_자료가_없으면_빈_표다(monkeypatch, cache_dir):
    monkeypatch.setattr(investor_flow.kis_api, "investor_detail",
                        lambda code, base, use_cache=True: pd.DataFrame())
    got = investor_flow.fetch("069500", "20260801", "20260814")
    assert got.empty
    assert list(got.columns) == ["투자자", "매도", "매수", "순매수"]
