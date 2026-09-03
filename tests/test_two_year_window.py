# -*- coding: utf-8 -*-
"""세이브로 2년치 수집분에서 **최근 12개월만** 주기 판정에 쓰는지 확인.

자르지 않으면 분기배당(2년 8회)이 '월'(7~39회)로 뒤집힌다 — 그걸 막는 테스트.
"""
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import classify


def _raw(dates: list[str], amounts: list[float] | None = None) -> pd.DataFrame:
    """세이브로 원본 모양의 최소 표 (분배주기 판정에 필요한 칸만)."""
    amounts = amounts or [100.0] * len(dates)
    return pd.DataFrame({
        "ISIN": ["KR7152100004"] * len(dates),
        "종목명": ["테스트ETF"] * len(dates),
        "운용사": ["삼성자산운용"] * len(dates),
        "유형": ["시장지수/코스피200"] * len(dates),
        "지급기준일": dates,
        "실지급일": dates,
        "결산과표기준가": [str(a) for a in amounts],
        "주당분배금": ["0.5"] * len(dates),      # 구 수집본에서는 분배율 자리
        "배당구분": ["이익분배"] * len(dates),
    })


def test_분기배당_2년치가_월로_뒤집히지_않는다():
    # 2년간 8회 = 자르지 않으면 '월'(7~39회). 최근 12개월 4회 → '분기' 여야 한다
    dates = ["20240930", "20241231", "20250331", "20250630",
             "20250930", "20251231", "20260331", "20260630"]
    out = classify.classify(_raw(dates))
    assert out.iloc[0]["분배주기"] == "분기"
    assert out.iloc[0]["지급횟수_12개월"] == 4


def test_연배당_2년치가_반기로_뒤집히지_않는다():
    out = classify.classify(_raw(["20250630", "20260630"]))
    assert out.iloc[0]["분배주기"] == "연"
    assert out.iloc[0]["지급횟수_12개월"] == 1


def test_월배당은_그대로_월이다():
    dates = [f"2025{m:02d}28" for m in range(1, 13)] + \
            [f"2026{m:02d}28" for m in range(1, 7)]
    out = classify.classify(_raw(dates))
    assert out.iloc[0]["분배주기"] == "월"
    assert out.iloc[0]["지급횟수_12개월"] == 12


def test_연간주당분배금은_최근_12개월_합이다():
    dates = ["20250930", "20251231", "20260331", "20260630"]
    out = classify.classify(_raw(dates, [100.0, 100.0, 200.0, 200.0]))
    assert out.iloc[0]["연간주당분배금"] == 600.0     # 4회 전부 최근 1년 안


def test_분배금성장률은_앞_12개월과_견준다():
    # 앞 12개월 400원(100×4) → 최근 12개월 800원(200×4) = +100%
    dates = ["20240930", "20241231", "20250331", "20250630",
             "20250930", "20251231", "20260331", "20260630"]
    amounts = [100.0] * 4 + [200.0] * 4
    out = classify.classify(_raw(dates, amounts))
    assert out.iloc[0]["분배금성장률"] == 100.0


def test_1년치만_있으면_성장률은_빈칸():
    out = classify.classify(_raw(["20260331", "20260630"]))
    assert pd.isna(out.iloc[0]["분배금성장률"])


def test_수집기간을_인자로_바꿀_수_있다():
    from datetime import date
    assert classify is not None
    import collect
    assert collect.rolling_year_start(date(2026, 8, 8), 2) == "20240808"
    assert collect.rolling_year_start(date(2026, 8, 8), 1) == "20250808"
