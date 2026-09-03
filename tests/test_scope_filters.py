# -*- coding: utf-8 -*-
"""사이드바 범위 선택(화이트리스트 / 보유종목) 테스트.

- portfolio.mark_holdings 가 '보유' 칸을 코드 우선으로 붙이는지
- dashboard.apply_scopes 가 두 범위를 서로 독립적으로 걸러내는지
"""
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import portfolio
from src import dashboard


def _etf_table() -> pd.DataFrame:
    return pd.DataFrame({
        "종목명": ["A배당ETF", "B배당ETF", "C배당ETF"],
        "KRX코드": ["152100", "069500", "360750"],
    })


def _holdings(**overrides) -> pd.DataFrame:
    base = {"계좌": ["연금", "일반"], "계좌유형": ["연금저축", "일반(위탁)"],
            "종목명": ["A배당ETF", "이름이 바뀐 ETF"], "KRX코드": ["A152100", ""],
            "수량": [10.0, 5.0], "매수단가": [1000.0, 2000.0],
            "매수일": [None, None], "메모": ["", ""]}
    base.update(overrides)
    return pd.DataFrame(base)


def test_보유_칸은_코드로_맞춘다():
    marked = portfolio.mark_holdings(_etf_table(), _holdings())
    assert list(marked["보유"]) == [True, False, False]      # 'A' 접두어를 떼고 맞춘다
    assert marked.loc[0, "보유수량"] == 10.0


def test_코드가_비면_종목명으로_맞춘다():
    holdings = _holdings(종목명=["A배당ETF", "B배당ETF"], KRX코드=["A152100", ""])
    marked = portfolio.mark_holdings(_etf_table(), holdings)
    assert list(marked["보유"]) == [True, True, False]
    assert marked.loc[1, "보유수량"] == 5.0


def test_같은_종목을_여러_계좌에_나눠_가지면_수량을_합친다():
    holdings = _holdings(계좌=["연금", "일반"], 종목명=["A배당ETF"] * 2,
                         KRX코드=["152100", "A152100"], 수량=[10.0, 7.0])
    marked = portfolio.mark_holdings(_etf_table(), holdings)
    assert marked.loc[0, "보유수량"] == 17.0


def test_보유종목_파일이_없으면_전부_False():
    marked = portfolio.mark_holdings(_etf_table(), portfolio.empty_holdings())
    assert not marked["보유"].any()


def _scoped() -> pd.DataFrame:
    return pd.DataFrame({
        "종목명": ["둘다", "화이트만", "보유만", "둘다아님"],
        "화이트리스트": [True, True, False, False],
        "보유": [True, False, True, False],
    })


def test_두_범위는_따로_걸린다():
    df = _scoped()
    only_wl = dashboard.apply_scopes(
        df, [("화이트리스트", "화이트리스트만", "화이트리스트만", "화이트리스트 제외")])
    assert list(only_wl["종목명"]) == ["둘다", "화이트만"]

    only_mine = dashboard.apply_scopes(
        df, [("보유", "보유종목만", "보유종목만", "보유종목 제외")])
    assert list(only_mine["종목명"]) == ["둘다", "보유만"]


def test_두_범위를_같이_고르면_겹치는_것만():
    # 화이트리스트만 + 보유종목 제외 = 아직 안 산 매매가능 종목
    out = dashboard.apply_scopes(_scoped(), [
        ("화이트리스트", "화이트리스트만", "화이트리스트만", "화이트리스트 제외"),
        ("보유", "보유종목 제외", "보유종목만", "보유종목 제외"),
    ])
    assert list(out["종목명"]) == ["화이트만"]


def test_투자지역은_한국_미국이_앞에_온다():
    df = pd.DataFrame({"투자지역": ["미국"] * 3 + ["중국"] * 5 + ["한국"] * 2 + [""]})
    # 종목 수는 중국이 제일 많아도 한국·미국을 앞에 세운다(처음에 켜 둘 값이라서)
    assert dashboard.region_options(df)[:2] == ["한국", "미국"]
    assert "중국" in dashboard.region_options(df)
    assert "" not in dashboard.region_options(df)


def test_전체를_고르면_안_걸러낸다():
    out = dashboard.apply_scopes(_scoped(), [
        ("화이트리스트", "전체", "화이트리스트만", "화이트리스트 제외"),
        ("보유", "전체", "보유종목만", "보유종목 제외"),
    ])
    assert len(out) == 4
