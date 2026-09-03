# -*- coding: utf-8 -*-
"""대안 비교 — 겹침 계산과 나란히 비교표.

겹침이 이 화면의 핵심이다. 이미 가진 것과 사실상 같은 ETF 로 갈아타면 비용만 들고
분산은 그대로다. 그래서 **겹침을 낮게 잡는 실수**(안 겹친다고 잘못 말하는 것)가
가장 위험하다 — 아래 테스트는 그 방향을 집중해서 막는다.
"""
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import compare


def _etf(**kwargs) -> pd.Series:
    base = {"종목명": "테스트ETF", "기초지수명": "", "투자지역": "미국",
            "테마섹터": "배당주", "자산분류": "주식", "전략": "일반",
            "시장분류": "해외", "상장시장": "미국",
            "총보수": 0.1, "시가총액": 1e12, "연간분배율": 3.0,
            "1년수익률": 10.0, "YTD수익률": 5.0, "분배주기": "분기"}
    base.update(kwargs)
    return pd.Series(base)


# ── 겹침 ─────────────────────────────────────────────
def test_기초지수가_같으면_100():
    """같은 지수를 따라가면 이름이 달라도 사실상 같은 물건이다."""
    a = _etf(종목명="A", 기초지수명="S&P 500", 테마섹터="혼합")
    b = _etf(종목명="B", 기초지수명="S&P 500", 테마섹터="기타", 전략="커버드콜")
    assert compare.overlap(a, b) == 100.0


def test_모든_축이_같으면_100():
    assert compare.overlap(_etf(), _etf()) == 100.0


def test_아무_축도_안_같으면_0():
    a = _etf(투자지역="미국", 테마섹터="배당주", 자산분류="주식",
             전략="일반", 시장분류="해외")
    b = _etf(투자지역="한국", 테마섹터="반도체", 자산분류="채권",
             전략="커버드콜", 시장분류="국내")
    assert compare.overlap(a, b) == 0.0


def test_지역과_섹터가_같으면_많이_겹친다():
    """지역·섹터는 무게가 커서, 그 둘만 같아도 절반을 넘어야 한다."""
    a = _etf(자산분류="주식", 전략="일반")
    b = _etf(자산분류="채권", 전략="커버드콜")
    assert compare.overlap(a, b) > 50


def test_빈_축은_분모에서_뺀다():
    """모르는 축을 '다르다'로 세면 자료가 부실한 종목이 안 겹치는 것처럼 보인다."""
    a = _etf(테마섹터="", 전략="")
    b = _etf(테마섹터="", 전략="")
    assert compare.overlap(a, b) == 100.0     # 남은 축(지역·자산·시장)이 다 같다


def test_축이_하나도_안_남으면_0():
    blank = _etf(투자지역="", 테마섹터="", 자산분류="", 전략="", 시장분류="")
    assert compare.overlap(blank, blank) == 0.0


def test_nan_문자열은_빈값으로_본다():
    """엑셀을 거치며 들어오는 'nan'·'None' 은 값이 아니라 빈칸이다.

    값으로 세면 둘 다 'nan' 인 축이 **일치**로 잡혀 겹침이 부풀고,
    기초지수명이 'nan' 인 두 종목이 같은 지수로 묶여 100%가 돼 버린다.
    """
    assert compare._text("nan") == ""
    assert compare._text("None") == ""
    assert compare._text(float("nan")) == ""

    # 기초지수명이 둘 다 비어 있으면 '같은 지수'로 보면 안 된다
    a = _etf(종목명="가", 기초지수명="nan", 투자지역="한국", 테마섹터="반도체",
             자산분류="채권", 전략="커버드콜", 시장분류="국내")
    b = _etf(종목명="나", 기초지수명="nan")
    assert compare.overlap(a, b) == 0.0


# ── 내 보유와의 겹침 ──────────────────────────────────
def test_보유_중_가장_많이_겹치는_것을_쓴다():
    """평균이면 하나와 90% 겹치는 사실이 묻힌다 → 최댓값이어야 한다."""
    candidates = pd.DataFrame([_etf(종목명="후보", 기초지수명="S&P 500")])
    holdings = pd.DataFrame([
        _etf(종목명="딴것", 투자지역="한국", 테마섹터="반도체",
             자산분류="채권", 전략="커버드콜", 시장분류="국내"),
        _etf(종목명="같은것", 기초지수명="S&P 500"),
    ])
    got = compare.overlap_with_holdings(candidates, holdings)
    assert got.iloc[0] == 100.0
    assert compare.best_match_name(candidates.iloc[0], holdings) == "같은것"


def test_보유가_없으면_빈칸이다():
    """0 으로 채우면 '안 겹친다'는 뜻이 돼서, 확인 안 한 것과 구분이 안 된다."""
    candidates = pd.DataFrame([_etf()])
    got = compare.overlap_with_holdings(candidates, pd.DataFrame())
    assert pd.isna(got.iloc[0])


# ── 나란히 비교표 ────────────────────────────────────
def test_지표가_행_종목이_열이다():
    picked = pd.DataFrame([_etf(종목명="가"), _etf(종목명="나")])
    table = compare.side_by_side(picked, "개요")
    assert list(table.columns) == ["가", "나"]
    assert "총보수" in table.index


def test_시가총액은_억으로_바꾼다():
    picked = pd.DataFrame([_etf(종목명="가", 시가총액=1e12)])
    table = compare.side_by_side(picked, "개요")
    assert table.loc["순자산(억)", "가"] == 10000.0


def test_총보수는_낮은_쪽이_이긴다():
    picked = pd.DataFrame([_etf(종목명="싼것", 총보수=0.03),
                           _etf(종목명="비싼것", 총보수=0.9)])
    assert compare.winners(picked, "개요")["총보수"] == "싼것"


def test_분배율은_높은_쪽이_이긴다():
    picked = pd.DataFrame([_etf(종목명="많이", 연간분배율=9.0),
                           _etf(종목명="적게", 연간분배율=1.0)])
    assert compare.winners(picked, "개요")["연분배율"] == "많이"


def test_원금반환은_낮은_쪽이_이긴다():
    """ROC 가 높은 건 좋은 게 아니라 원금을 돌려받고 있다는 뜻이다."""
    picked = pd.DataFrame([
        _etf(종목명="ROC많음", **{"누적_원금반환비율": 99.0}),
        _etf(종목명="ROC적음", **{"누적_원금반환비율": 5.0}),
    ])
    assert compare.winners(picked, "배당")["원금반환(ROC)"] == "ROC적음"


def test_좋고_나쁨을_못_따지는_지표엔_승자가_없다():
    picked = pd.DataFrame([_etf(종목명="가", 베타=1.4), _etf(종목명="나", 베타=0.6)])
    assert "베타" not in compare.winners(picked, "리스크")


def test_값이_다_비면_승자가_없다():
    picked = pd.DataFrame([_etf(종목명="가", 총보수=None),
                           _etf(종목명="나", 총보수=None)])
    assert "총보수" not in compare.winners(picked, "개요")


def test_총수익_파생칸이_생긴다():
    frame = compare.add_total_return(pd.DataFrame([_etf(**{"1년수익률": 10.0,
                                                          "연간분배율": 3.0})]))
    assert frame["_총수익1년"].iloc[0] == 13.0


def test_모든_뷰가_비지_않는다():
    """뷰를 추가할 때 칸 이름을 틀리면 표가 통째로 비는데, 그걸 잡는다."""
    picked = pd.DataFrame([_etf(종목명="가")])
    for view in compare.VIEWS:
        table = compare.side_by_side(picked, view)
        assert not table.empty, f"{view} 뷰가 비었습니다"
        assert len(table.index) == len(compare.VIEWS[view])
