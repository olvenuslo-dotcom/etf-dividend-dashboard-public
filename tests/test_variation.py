# -*- coding: utf-8 -*-
"""분배금 변동계수 — 회차별 금액이 얼마나 들쭉날쭉한가 (ROC 대용 신호).

핵심은 두 가지다.
1) 금액 크기·통화에 흔들리지 않아야 한다 (평균으로 나누니까) — 원화 환산된
   미국 상장분을 국내와 같은 자로 비교할 수 있는 근거다.
2) 지급 횟수가 모자라면 **비워야** 한다. 2회짜리 표준편차는 숫자만 나올 뿐
   '들쭉날쭉하다'는 뜻이 못 된다.
"""
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import classify


def _raw(dates: list[str], amounts: list[float]) -> pd.DataFrame:
    """세이브로 원본 모양의 최소 표 (구 수집본: 금액이 '결산과표기준가')."""
    return pd.DataFrame({
        "ISIN": ["KR7152100004"] * len(dates),
        "종목명": ["테스트ETF"] * len(dates),
        "운용사": ["삼성자산운용"] * len(dates),
        "유형": ["시장지수/코스피200"] * len(dates),
        "지급기준일": dates,
        "실지급일": dates,
        "결산과표기준가": [str(a) for a in amounts],
        "주당분배금": ["0.5"] * len(dates),
        "배당구분": ["이익분배"] * len(dates),
    })


def test_금액이_일정하면_0이다():
    assert classify.variation_coefficient([100, 100, 100, 100]) == 0.0


def test_들쭉날쭉하면_값이_커진다():
    고른값 = classify.variation_coefficient([100, 105, 95, 100])
    출렁임 = classify.variation_coefficient([10, 300, 40, 250])
    assert 출렁임 > 고른값
    assert 출렁임 > 50


def test_금액_크기와_통화에_무관하다():
    """평균으로 나누므로 배수가 약분된다 — 원화 환산 미국분도 같은 자로 잰다."""
    원 = classify.variation_coefficient([1000, 3000, 2000, 4000])
    달러 = classify.variation_coefficient([1, 3, 2, 4])
    assert 원 == 달러


def test_지급이_3회_미만이면_비운다():
    assert classify.variation_coefficient([100, 200]) is None
    assert classify.variation_coefficient([100]) is None
    assert classify.variation_coefficient([]) is None
    assert classify.variation_coefficient([100, 200, 300]) is not None


def test_평균이_0이면_비운다():
    """분모가 0이면 나눌 수 없다 — 무한대 대신 빈칸."""
    assert classify.variation_coefficient([0, 0, 0]) is None


def test_숫자가_아닌_값은_빼고_센다():
    """빈칸이 섞여도 죽지 않는다. 남은 게 3회 미만이면 비운다."""
    assert classify.variation_coefficient([100, None, 100, 100]) == 0.0
    assert classify.variation_coefficient([100, None, None, 200]) is None


def test_분류표에_칸이_생긴다():
    """월배당 12회 중 한 번만 크게 준 종목 → 값이 채워지고 0보다 크다."""
    dates = [f"2025{m:02d}28" for m in range(9, 13)] + \
            [f"2026{m:02d}28" for m in range(1, 9)]
    amounts = [100.0] * 11 + [900.0]
    out = classify.classify(_raw(dates, amounts))
    assert out.iloc[0]["지급횟수_12개월"] == 12
    assert out.iloc[0]["분배변동계수"] > 100


def test_최근_12개월만_본다():
    """앞 12개월이 아무리 출렁여도 최근 1년이 고르면 값은 0이다.

    분배주기 판정과 **같은 창**을 써야 표에서 앞뒤가 맞는다.
    """
    old = [f"2024{m:02d}28" for m in range(9, 13)] + \
          [f"2025{m:02d}28" for m in range(1, 9)]
    new = [f"2025{m:02d}28" for m in range(9, 13)] + \
          [f"2026{m:02d}28" for m in range(1, 9)]
    amounts = [10.0, 900.0] * 6 + [100.0] * 12      # 앞 12개월만 출렁인다
    out = classify.classify(_raw(old + new, amounts))
    assert out.iloc[0]["분배변동계수"] == 0.0


def test_반기배당은_비어_있다():
    """1년에 2회면 표본이 모자라 판단하지 않는다."""
    out = classify.classify(_raw(["20250630", "20251231"], [100.0, 500.0]))
    assert out.iloc[0]["분배주기"] == "반기"
    assert pd.isna(out.iloc[0]["분배변동계수"])
