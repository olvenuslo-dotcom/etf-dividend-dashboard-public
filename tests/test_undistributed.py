# -*- coding: utf-8 -*-
"""분배 이력 없는 상장 ETF('미분배')를 후보군으로 덧붙이는 부분 테스트."""
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import classify


def _classified() -> pd.DataFrame:
    """분배 이력이 있어 이미 분류된 종목 하나."""
    row = {c: None for c in classify.OUTPUT_COLUMNS}
    row.update({"종목명": "A배당ETF", "운용사": "미래에셋", "국내해외": "국내",
                "분배주기": "월", "지급횟수_12개월": 12, "ISIN": "KR7000001001"})
    return pd.DataFrame([row], columns=classify.OUTPUT_COLUMNS)


def _info() -> pd.DataFrame:
    """KRX 전종목 정보 — 위 한 종목 + 분배 이력이 없는 두 종목."""
    return pd.DataFrame({
        "ISIN": ["KR7000001001", "KR7000002002", "KR7000003003"],
        "KRX코드": ["000001", "000002", "000003"],
        "종목명": ["A배당ETF", "B성장ETF", "C해외ETF"],
        "운용사": ["미래에셋자산운용", "삼성자산운용", "한국투자신탁운용"],
        "시가총액": [1e10, 2e10, 3e10],
        "총보수": [0.15, 0.30, 0.45],
        "자산분류": ["주식"] * 3,
        "시장분류": ["국내", "국내", "해외"],
        "투자지역": ["한국", "한국", "미국"],
        "테마섹터": ["배당주", "AI·테크", "혼합"],
        "전략": ["일반"] * 3,
        "운용방식": ["실물(패시브)"] * 3,
        "과세유형": ["비과세"] * 3,
        "기초지수명": ["지수1", "지수2", "지수3"],
    })


def test_이력_없는_종목만_미분배로_붙는다():
    out = classify.add_undistributed(_classified(), _info())
    assert len(out) == 3
    미분배 = out[out["분배주기"] == classify.NO_CYCLE]
    assert sorted(미분배["종목명"]) == ["B성장ETF", "C해외ETF"]
    # 이미 있던 종목은 그대로 (중복으로 다시 안 붙는다)
    assert (out["ISIN"] == "KR7000001001").sum() == 1


def test_미분배는_분배칸이_비어있다():
    out = classify.add_undistributed(_classified(), _info())
    row = out[out["종목명"] == "B성장ETF"].iloc[0]
    assert row["지급횟수_12개월"] == 0
    for column in ("최근_주당분배금", "최근_분배율", "최근_지급기준일", "최근_매수마감일"):
        assert pd.isna(row[column])


def test_국내해외는_KRX_시장분류로_정한다():
    # 종목명이 아니라 데이터 값으로 판단 (CLAUDE.md 규칙)
    out = classify.add_undistributed(_classified(), _info())
    assert out.set_index("종목명").loc["C해외ETF", "국내해외"] == "해외"
    assert out.set_index("종목명").loc["B성장ETF", "국내해외"] == "국내"


def test_KRX_분류값을_그대로_가져온다():
    out = classify.add_undistributed(_classified(), _info())
    row = out[out["종목명"] == "C해외ETF"].iloc[0]
    assert row["투자지역"] == "미국" and row["총보수"] == 0.45


def test_운용사는_짧게_줄인다():
    out = classify.add_undistributed(_classified(), _info())
    assert out.set_index("종목명").loc["B성장ETF", "운용사"] == "삼성"


def test_미분배는_맨_뒤로_정렬된다():
    out = classify.add_undistributed(_classified(), _info())
    assert out["분배주기"].iloc[0] == "월"
    assert out["분배주기"].iloc[-1] == classify.NO_CYCLE


def test_옛_KRX_CSV_처럼_종목명이_없으면_아무것도_안_붙인다():
    info = _info().drop(columns="종목명")
    out = classify.add_undistributed(_classified(), info)
    assert len(out) == 1
