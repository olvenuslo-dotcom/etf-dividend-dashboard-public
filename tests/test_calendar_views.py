# -*- coding: utf-8 -*-
"""달력 보기 / 월별 건수 그래프의 계산 부분 테스트 (화면 그리기는 AppTest 로 따로 확인)."""
from datetime import date
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from src import dashboard


def _events() -> pd.DataFrame:
    return pd.DataFrame({
        "날짜": [date(2026, 8, 12), date(2026, 8, 12), date(2026, 8, 28),
                 date(2026, 9, 1), date(2026, 9, 30)],
        "종류": ["매수", "배당", "매수", "배당", "배당"],
        "종목명": ["A ETF", "B ETF", "C ETF", "D ETF", "E ETF"],
        "주기": ["월", "위클리", "월", "분기", "월"],
    })


def test_일정이_든_달만_고를_수_있다():
    assert dashboard.month_options(_events()) == [(2026, 8), (2026, 9)]


def test_날짜별로_종류를_나눠_센다():
    cells = dashboard.calendar_cells(_events(), 2026, 8)
    assert sorted(cells) == [12, 28]
    assert cells[12] == {"매수": ["A ETF"], "배당": ["B ETF"]}
    assert cells[28] == {"매수": ["C ETF"]}


def test_다른_달은_안_섞인다():
    cells = dashboard.calendar_cells(_events(), 2026, 9)
    assert sorted(cells) == [1, 30]


def test_달력_HTML_은_7열_격자와_배지를_만든다():
    cells = dashboard.calendar_cells(_events(), 2026, 8)
    html = dashboard.calendar_html(cells, 2026, 8, date(2026, 8, 5),
                                   dashboard.KIND_COLORS["light"], ["매수", "배당"])
    assert html.count('class="hd') == 7                  # 요일 머리글 7개
    assert html.count('class="cell') == 42               # 2026-08 은 6주 × 7일
    assert "매수 1" in html and "배당 1" in html          # 색만이 아니라 글자도 넣는다
    assert "today" in html                               # 오늘(8/5) 칸 표시
    assert "A ETF" in html


def test_종목명은_HTML_로_안_새게_이스케이프한다():
    events = _events().assign(종목명=["<b>나쁜 이름</b>"] + ["X"] * 4)
    cells = dashboard.calendar_cells(events, 2026, 8)
    html = dashboard.calendar_html(cells, 2026, 8, date(2026, 8, 5),
                                   dashboard.KIND_COLORS["light"], ["매수"])
    assert "<b>나쁜" not in html
    assert "&lt;b&gt;" in html


def test_월별_건수는_주기별로_나뉜다():
    agg = dashboard.monthly_counts(_events(), "배당")
    assert list(agg["월"]) == ["26-08", "26-09", "26-09"]
    assert agg["건수"].sum() == 3
    assert set(agg["주기"]) == {"주", "분기", "월"}      # 위클리 → '주' 로 표시


def test_주기에_월이_있어도_표가_안_부딪힌다():
    # '월'(기간) 칸과 '월'(분배주기) 칸 이름이 겹쳐 표가 깨지던 자리
    agg = dashboard.monthly_counts(_events(), "배당")
    order = [dashboard.CYCLE_LABEL[c] for c in dashboard.CYCLE_ORDER] + ["미상"]
    wide = dashboard.monthly_counts_wide(agg, order)
    assert list(wide.columns)[0] == "월"
    assert "월 분배" in wide.columns and "합계" in wide.columns
    assert wide["합계"].sum() == 3


def test_주기색은_주기마다_고정이다():
    # 필터로 종목이 줄어도 '월'은 늘 같은 색이어야 한다
    light = dashboard.CYCLE_COLORS["light"]
    assert set(light) == {"주", "월", "분기", "연3회", "반기", "연", "미분배"}
    # 분배주기 6색은 서로 달라야 한다 ('미분배'는 자료 없음이라 회색 하나로 둔다)
    분배색 = [light[c] for c in ("주", "월", "분기", "연3회", "반기", "연")]
    assert len(set(분배색)) == 6
    assert set(dashboard.CYCLE_COLORS["dark"]) == set(light)
