# -*- coding: utf-8 -*-
"""KPI 차트 클릭 → 아래에 종목 목록 펼치기.

여기서 지키려는 것:
1) **선택 이벤트가 dict 가 아닐 수 있다.** Streamlit 이 주는 것은
   `AttributeDictionary` 라 `isinstance(x, dict)` 로 거르면 항상 빈 값이 된다
   — 클릭해도 아무것도 안 뜨는 조용한 고장이다.
2) 막대는 `x`, 파이 조각은 `label` 에 이름이 들어 있다 — 둘 다 읽어야 한다.
3) 차트는 짧은 이름('주')으로 그렸다 → 데이터 값('위클리')으로 되돌려 걸러야 한다.
"""
from pathlib import Path
import sys

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from src.dashboard import chart_selection_labels


class _Attr:
    """Streamlit 의 AttributeDictionary 흉내 — dict 가 **아니다**."""

    def __init__(self, data):
        self._data = data

    def __getitem__(self, key):
        value = self._data[key]
        return _Attr(value) if isinstance(value, dict) else value

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError as exc:
            raise AttributeError(key) from exc


def _event(points, wrap=False):
    payload = {"selection": {"points": points, "box": [], "lasso": []}}
    return _Attr(payload) if wrap else payload


# ── 선택 이벤트 읽기 ────────────────────────────────────
def test_막대는_x축_값에서_이름을_읽는다():
    got = chart_selection_labels(_event([{"x": "월", "y": 792}]))
    assert got == ["월"]


def test_파이는_label_에서_이름을_읽는다():
    got = chart_selection_labels(_event([{"label": "국내", "value": 566}]))
    assert got == ["국내"]


def test_dict_가_아닌_이벤트도_읽는다():
    """실제 Streamlit 이 주는 모양 — 이걸 놓치면 클릭이 조용히 무시된다."""
    got = chart_selection_labels(_event([{"x": "분기"}], wrap=True))
    assert got == ["분기"]


def test_아무것도_안_고르면_빈_목록():
    assert chart_selection_labels(_event([])) == []
    assert chart_selection_labels(None) == []
    assert chart_selection_labels({}) == []


def test_같은_칸을_여러_번_고르면_한_번만():
    got = chart_selection_labels(_event([{"x": "월"}, {"x": "월"}, {"x": "분기"}]))
    assert got == ["월", "분기"]


def test_빈_이름은_버린다():
    got = chart_selection_labels(_event([{"x": "  "}, {"x": "연"}, {"label": None}]))
    assert got == ["연"]


# ── 화면 이름 ↔ 데이터 값 ───────────────────────────────
def test_차트의_주는_데이터의_위클리로_되돌아온다():
    """차트는 '주'로 그리고 데이터는 '위클리'다 — 안 되돌리면 걸러도 0개가 나온다."""
    from src.dashboard import chart_picks

    cycles, domestics = chart_picks(_event([{"x": "주"}]), _event([]))
    assert cycles == ["위클리"]
    assert domestics == []


def test_국내해외는_그대로_온다():
    from src.dashboard import chart_picks

    cycles, domestics = chart_picks(_event([]), _event([{"x": "국내"}]))
    assert cycles == []
    assert domestics == ["국내"]


# ── 고른 기준이 실제로 표를 좁힌다 ──────────────────────
def _view():
    return pd.DataFrame({
        "종목명": ["A", "B", "C", "D"],
        "분배주기": ["위클리", "월", "위클리", "분기"],
        "국내해외": ["국내", "국내", "해외", "해외"],
    })


def test_주기를_고르면_그것만_남는다():
    from src.dashboard import apply_chart_picks

    got = apply_chart_picks(_view(), ["위클리"], [])
    assert list(got["종목명"]) == ["A", "C"]


def test_둘_다_고르면_겹치는_것만_남는다():
    from src.dashboard import apply_chart_picks

    got = apply_chart_picks(_view(), ["위클리"], ["국내"])
    assert list(got["종목명"]) == ["A"]


def test_아무것도_안_고르면_그대로다():
    from src.dashboard import apply_chart_picks

    got = apply_chart_picks(_view(), [], [])
    assert len(got) == 4


def test_겹치는_것이_없으면_빈_표다():
    from src.dashboard import apply_chart_picks

    got = apply_chart_picks(_view(), ["분기"], ["국내"])
    assert got.empty


# ── 두 차트의 가로 폭 ───────────────────────────────────
# 폭을 반반으로 두면 칸이 적은 쪽(국내/해외 2칸)은 한 칸이 넓어져 막대가 두껍고
# 서로 멀찍이 떨어진다. 폭 자체를 칸 수에 맞추면 두께·간격이 같이 맞는다.
def test_폭은_칸_수에_비례한다():
    from src.dashboard import chart_column_ratio

    assert chart_column_ratio(7, 2) == [7, 2]
    assert chart_column_ratio(7, 1) == [7, 2]      # 최소 폭 보장
    assert chart_column_ratio(5, 3) == [5, 3]


def test_빈_표여도_폭이_0이_되지_않는다():
    """0 을 주면 st.columns 가 죽는다."""
    from src.dashboard import MIN_CHART_SLOTS, chart_column_ratio

    got = chart_column_ratio(0, 0)
    assert got == [MIN_CHART_SLOTS, MIN_CHART_SLOTS]
    assert all(v > 0 for v in got)
