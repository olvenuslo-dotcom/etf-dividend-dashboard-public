# -*- coding: utf-8 -*-
"""NAV 비교표 조회 순서 테스트: 정렬 기준별 상위 종목 / 결측값 처리."""
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from dashboard import COMPARE_ORDER, picked_row_index, sort_candidates


def _rows() -> pd.DataFrame:
    """시총·분배율·총보수·지급횟수가 서로 다른 4종목 (D는 값 결측)."""
    return pd.DataFrame({
        "종목명": ["A", "B", "C", "D"],
        "시가총액": [100.0, 3000.0, 500.0, None],
        "최근_분배율": [1.0, 0.5, 4.0, None],
        "총보수": [0.5, 0.09, 0.2, None],
        "지급횟수_12개월": [4, 12, 1, None],
        "YTD수익률": [-3.0, 8.5, 1.0, None],
        "1년수익률": [12.0, -2.0, 5.0, None],
    })


def test_시총_큰_순은_내림차순():
    assert list(sort_candidates(_rows(), "시총 큰 순")["종목명"])[:3] == ["B", "C", "A"]


def test_시총_작은_순은_오름차순():
    assert list(sort_candidates(_rows(), "시총 작은 순")["종목명"])[:3] == ["A", "C", "B"]


def test_분배율_높은_순():
    assert list(sort_candidates(_rows(), "분배율 높은 순")["종목명"])[:3] == ["C", "A", "B"]


def test_총보수_낮은_순():
    assert list(sort_candidates(_rows(), "총보수 낮은 순")["종목명"])[:3] == ["B", "C", "A"]


def test_지급_횟수_많은_순():
    assert list(sort_candidates(_rows(), "지급 횟수 많은 순")["종목명"])[:3] == ["B", "A", "C"]


def test_YTD_수익률_높은_순():
    assert list(sort_candidates(_rows(), "YTD 수익률 높은 순")["종목명"])[:3] == ["B", "C", "A"]


def test_1년_수익률_높은_순():
    assert list(sort_candidates(_rows(), "1년 수익률 높은 순")["종목명"])[:3] == ["A", "C", "B"]


def test_값_없는_종목은_항상_맨_뒤():
    # 오름차순(작은 순)에서도 결측이 앞으로 오면 안 된다
    for label in [l for l in COMPARE_ORDER if COMPARE_ORDER[l][0] is not None]:
        assert list(sort_candidates(_rows(), label)["종목명"])[-1] == "D", label


def test_종목명_순은_원본_순서_유지():
    # 표는 이미 주기→종목명으로 정렬돼 있으므로 다시 건드리지 않는다
    assert list(sort_candidates(_rows(), "종목명 순")["종목명"]) == ["A", "B", "C", "D"]


def test_정렬_컬럼이_없어도_죽지_않는다():
    # 시총·총보수가 결합되지 않은 산출물(etf_info.py 미실행)에서도 동작해야 한다
    bare = _rows().drop(columns=["시가총액", "총보수"])
    assert list(sort_candidates(bare, "시총 큰 순")["종목명"]) == ["A", "B", "C", "D"]


def test_모든_선택지가_정렬_가능():
    for label in COMPARE_ORDER:
        assert len(sort_candidates(_rows(), label)) == 4, label


def test_모든_기준에_설명이_있다():
    # '?' 로 정의를 찾아볼 수 있어야 하므로 설명이 비면 안 된다
    for label, (_, _, desc) in COMPARE_ORDER.items():
        assert desc and len(desc) > 10, label


def test_원본_데이터프레임은_변형되지_않는다():
    df = _rows()
    sort_candidates(df, "시총 큰 순")
    assert list(df["종목명"]) == ["A", "B", "C", "D"]
    assert "_key" not in df.columns


# ── 행 클릭(셀 선택)으로도 상세가 열려야 한다 ──────────────
class _Sel:
    def __init__(self, rows=None, cells=None):
        self.rows, self.cells = rows or [], cells or []


class _Event:
    def __init__(self, selection):
        self.selection = selection


def test_체크박스로_고른_행():
    assert picked_row_index(_Event(_Sel(rows=[3]))) == 3


def test_셀을_클릭해도_그_행이_잡힌다():
    # 체크박스를 안 눌러도 아무 칸이나 누르면 (행번호, 컬럼명) 이 온다
    assert picked_row_index(_Event(_Sel(cells=[(2, "NAV변화")]))) == 2


def test_체크박스가_셀보다_우선():
    assert picked_row_index(_Event(_Sel(rows=[1], cells=[(5, "주기")]))) == 1


def test_아무것도_안_골랐으면_None():
    assert picked_row_index(_Event(_Sel())) is None
