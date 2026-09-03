# -*- coding: utf-8 -*-
"""환율 시나리오 — 미국 종목 원화 금액만 다시 계산하는지 확인."""
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import watch


def _planned() -> pd.DataFrame:
    return pd.DataFrame({
        "종목명": ["국내월배당ETF", "US Dividend ETF"],
        "코드": ["152100", "SCHD"],
        "시장": ["한국", "미국"],
        "현재가": [10_000.0, 40_000.0],
        "투자금액": [100_000.0, 200_000.0],
        "예상연분배금": [12_000.0, 6_000.0],
        "예상연분배금_세후": [10_152.0, 5_100.0],
        "예상연분배율": [12.0, 3.0],
    })


def test_미국_금액만_환율만큼_바뀐다():
    out = watch.apply_fx(_planned(), new_rate=1_500.0, old_rate=1_000.0)  # +50%
    kr = out[out["시장"] == "한국"].iloc[0]
    us = out[out["시장"] == "미국"].iloc[0]
    assert kr["투자금액"] == 100_000.0            # 국내는 그대로
    assert kr["예상연분배금"] == 12_000.0
    assert us["투자금액"] == 300_000.0            # 미국만 1.5배
    assert us["현재가"] == 60_000.0
    assert us["예상연분배금"] == 9_000.0
    assert us["예상연분배금_세후"] == 7_650.0


def test_분배율은_환율과_무관하다():
    # 투자금액과 분배금이 같은 비율로 움직이므로 %는 변하면 안 된다
    out = watch.apply_fx(_planned(), new_rate=1_700.0, old_rate=1_000.0)
    assert list(out["예상연분배율"]) == [12.0, 3.0]


def test_환율이_같으면_그대로():
    out = watch.apply_fx(_planned(), new_rate=1_400.0, old_rate=1_400.0)
    pd.testing.assert_frame_equal(out, _planned())


# ── 총수익 시나리오 — 사고 난 뒤 환율이 움직이면 ─────────
def test_산_뒤_환율이_오르면_원금에_환차익이_생긴다():
    got = watch.fx_total_return(_planned(), new_rate=1_500.0, old_rate=1_000.0)
    assert got["투자금액"] == 200_000.0           # 미국 종목만
    assert got["환차손익"] == 100_000.0           # 200,000 × 50%
    assert got["연분배금_후"] == 7_650.0          # 세후 5,100 × 1.5
    assert got["총효과"] == 107_650.0
    assert got["총효과_환율_그대로"] == 5_100.0   # 견줄 기준


def test_환율이_그대로면_환차손익은_0이다():
    got = watch.fx_total_return(_planned(), new_rate=1_000.0, old_rate=1_000.0)
    assert got["환차손익"] == 0.0
    assert got["총효과"] == got["총효과_환율_그대로"]


def test_미국_종목이_없으면_총수익은_None이다():
    """0 으로 채우면 '효과 없음'과 구분이 안 된다 — 아예 없다고 말한다."""
    kr_only = _planned().head(1)
    assert watch.fx_total_return(kr_only, new_rate=1_500.0, old_rate=1_000.0) is None


def test_환율이_없으면_그대로():
    for bad in (0, None):
        out = watch.apply_fx(_planned(), new_rate=bad, old_rate=1_400.0)
        pd.testing.assert_frame_equal(out, _planned())


def test_시장_칸이_없으면_손대지_않는다():
    frame = _planned().drop(columns="시장")
    out = watch.apply_fx(frame, new_rate=2_000.0, old_rate=1_000.0)
    pd.testing.assert_frame_equal(out, frame)


def test_지급일정_금액도_바뀐다():
    upcoming = pd.DataFrame({
        "종목명": ["국내월배당ETF", "US Dividend ETF"],
        "시장": ["한국", "미국"],
        "세전": [1_000.0, 1_500.0],
        "세후": [846.0, 1_275.0],
    })
    out = watch.apply_fx(upcoming, new_rate=2_000.0, old_rate=1_000.0)
    assert list(out["세전"]) == [1_000.0, 3_000.0]
    assert list(out["세후"]) == [846.0, 2_550.0]
