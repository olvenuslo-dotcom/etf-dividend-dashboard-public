# -*- coding: utf-8 -*-
"""장기 상장주식수 — 분배 이력에서 되짚어 낸 설정·환매 흐름.

핵심은 셋이다.
1) `총분배금 ÷ 주당분배금` 이 그 분배를 받을 권리가 있던 주식 수다.
2) 그 값이 KRX 상장주식수에 잡히는 자리는 **기준일이 아니라 다음 개장일**이다.
   (실측: 캐시와 겹치는 826건이 당일 94.7% → 다음 개장일 100% 일치)
3) 점이 하나뿐이면 **증감을 말하지 않는다.** 한 점으로 '늘었다/줄었다'는 없다.
"""
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from flow import long_shares as ls


def _raw(dates: list[str], per_share: list[float], total: list[float],
         kind: list[str] | None = None,
         isin: str = "KR7152100004") -> pd.DataFrame:
    """세이브로 분배상세 원본 모양의 최소 표."""
    return pd.DataFrame({
        "ISIN": [isin] * len(dates),
        "종목명": ["테스트ETF"] * len(dates),
        "지급기준일": dates,
        "배당구분": kind or ["이익분배"] * len(dates),
        "주당분배금": [str(v) for v in per_share],
        "총분배금": [str(v) for v in total],
    })


# ── 계산 ────────────────────────────────────────────────
def test_총분배금을_주당분배금으로_나누면_주식수다():
    got = ls.derive(_raw(["20250430"], [300], [840_000_000]))
    assert len(got) == 1
    assert got["상장주식수"].iloc[0] == 2_800_000


def test_KRX코드는_ISIN_가운데_여섯자리다():
    got = ls.derive(_raw(["20250430"], [300], [840_000_000]))
    assert got["KRX코드"].iloc[0] == "152100"


def test_청산분배는_뺀다():
    """청산은 분배가 아니라 정리다 — 다른 모듈과 같은 규칙."""
    got = ls.derive(_raw(["20250430", "20250731"], [300, 100],
                         [840_000_000, 280_000_000],
                         kind=["이익분배", "청산분배"]))
    assert len(got) == 1
    assert got["지급기준일"].iloc[0] == pd.Timestamp("2025-04-30")


def test_주당분배금이_0이면_버린다():
    """0 으로는 나눌 수 없다. 억지로 채우지 않고 그 회차를 뺀다."""
    got = ls.derive(_raw(["20250430", "20250731"], [0, 300],
                         [840_000_000, 840_000_000]))
    assert len(got) == 1
    assert got["지급기준일"].iloc[0] == pd.Timestamp("2025-07-31")


def test_총분배금이_비면_버린다():
    frame = _raw(["20250430"], [300], [840_000_000])
    frame.loc[0, "총분배금"] = None
    assert ls.derive(frame).empty


def test_같은_기준일이_두번_와도_한_점이다():
    got = ls.derive(_raw(["20250430", "20250430"], [300, 300],
                         [840_000_000, 840_000_000]))
    assert len(got) == 1


# ── 날짜 (핵심) ──────────────────────────────────────────
def test_날짜는_기준일_다음_개장일이다():
    """2025-04-30(수) 기준일 → 5/1 은 근로자의날 휴장 → 5/2(금)."""
    got = ls.derive(_raw(["20250430"], [300], [840_000_000]))
    assert got["날짜"].iloc[0] == pd.Timestamp("2025-05-02")
    assert got["지급기준일"].iloc[0] == pd.Timestamp("2025-04-30")


def test_금요일_기준일은_다음주_월요일로_간다():
    """2025-07-31 은 목요일 → 8/1 금요일."""
    got = ls.derive(_raw(["20250801"], [100], [200_000_000]))   # 8/1 금
    assert got["날짜"].iloc[0] == pd.Timestamp("2025-08-04")     # 8/4 월


# ── 증감 ────────────────────────────────────────────────
def _long():
    return ls.derive(_raw(["20250131", "20250430", "20250731", "20251031"],
                          [100, 100, 100, 100],
                          [100_000_000, 120_000_000, 150_000_000, 200_000_000]))


def test_증감은_처음과_끝의_차이다():
    got = ls.growth(_long(), "152100")
    assert got["시작"] == 1_000_000
    assert got["끝"] == 2_000_000
    assert got["증감"] == 1_000_000
    assert round(got["증감률"], 1) == 100.0
    assert got["점개수"] == 4


def test_점이_하나면_증감을_내지_않는다():
    """한 점으로는 '늘었다/줄었다'를 말할 수 없다 — 0 이 아니라 None 이다."""
    got = ls.growth(ls.derive(_raw(["20250131"], [100], [100_000_000])), "152100")
    assert got["증감률"] is None
    assert got["증감"] is None
    assert got["점개수"] == 1


def test_없는_종목은_빈_값이다():
    got = ls.growth(_long(), "999999")
    assert got["점개수"] == 0
    assert got["증감률"] is None
    assert ls.series(_long(), "999999").empty


def test_창은_마지막_점_기준으로_거슬러_올라간다():
    """오늘 기준으로 자르면 분배가 끊긴 종목의 창이 통째로 비어 버린다."""
    got = ls.growth(_long(), "152100", months=6)
    # 마지막 점(2025-11-03) 에서 6개월 → 2025-05-03 이후 = 뒤 두 점
    assert got["점개수"] == 2
    assert got["시작"] == 1_500_000
    assert got["끝"] == 2_000_000


def test_창_밖이면_점이_모자라_증감이_없다():
    got = ls.growth(_long(), "152100", months=1)
    assert got["증감률"] is None


# ── 빈 입력 (앱을 죽이지 않는다) ─────────────────────────
def test_빈_표를_넣어도_안_죽는다():
    assert ls.derive(pd.DataFrame()).empty
    assert ls.series(pd.DataFrame(), "152100").empty
    assert ls.growth(pd.DataFrame(), "152100")["점개수"] == 0
    assert ls.coverage(pd.DataFrame())["종목수"] == 0


# ── 디스크 캐시 ─────────────────────────────────────────
# `derive()` 는 XKRX 달력을 지어서 23초 걸린다. 대시보드 첫 화면에 그대로 얹으면
# 못 쓴다. 그래서 결과를 CSV 로 캐시하는데, **낡은 값을 물고 있으면 안 된다.**
def _write_source(folder, name="seibro_분배상세_20260101.csv", total=100_000_000):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    _raw(["20250131", "20250430"], [100, 100],
         [total, 120_000_000]).to_csv(path, index=False, encoding="utf-8-sig")
    return path


def _redirect(monkeypatch, tmp_path):
    monkeypatch.setattr(ls, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(ls, "CACHE_DIR", tmp_path / "cache")


def test_캐시를_만들고_다시_읽는다(monkeypatch, tmp_path):
    _redirect(monkeypatch, tmp_path)
    source = _write_source(tmp_path / "raw")

    first = ls.load()
    cache = ls._cache_path(source)
    assert cache.exists()

    second = ls.load()
    assert list(first.columns) == list(second.columns)
    assert first["상장주식수"].tolist() == second["상장주식수"].tolist()
    # 날짜가 글자로 돌아오면 growth() 의 창 자르기가 깨진다
    assert str(second["날짜"].dtype).startswith("datetime64")


def test_원본이_바뀌면_캐시를_버린다(monkeypatch, tmp_path):
    """캐시가 원본보다 오래됐으면 다시 만든다 — 안 그러면 옛 값이 그대로 남는다."""
    import os
    import time

    _redirect(monkeypatch, tmp_path)
    source = _write_source(tmp_path / "raw", total=100_000_000)
    assert ls.load()["상장주식수"].iloc[0] == 1_000_000

    # 원본을 고치고, 캐시보다 확실히 새 것으로 만든다
    _write_source(tmp_path / "raw", total=500_000_000)
    later = time.time() + 10
    os.utime(source, (later, later))

    assert ls.load()["상장주식수"].iloc[0] == 5_000_000


def test_원본이_다른_파일이면_캐시도_따로다(monkeypatch, tmp_path):
    """새로 수집하면 파일명이 바뀐다. 캐시가 섞이면 안 된다."""
    _redirect(monkeypatch, tmp_path)
    old = _write_source(tmp_path / "raw", "seibro_분배상세_20260101.csv",
                        total=100_000_000)
    ls.load()
    new = _write_source(tmp_path / "raw", "seibro_분배상세_20260202.csv",
                        total=900_000_000)
    assert ls._cache_path(old) != ls._cache_path(new)
    assert ls.load()["상장주식수"].iloc[0] == 9_000_000


def test_캐시를_꺼도_같은_값이_나온다(monkeypatch, tmp_path):
    _redirect(monkeypatch, tmp_path)
    _write_source(tmp_path / "raw")
    assert (ls.load(use_cache=False)["상장주식수"].tolist()
            == ls.load(use_cache=True)["상장주식수"].tolist())


def test_원본이_없으면_빈_표다(monkeypatch, tmp_path):
    _redirect(monkeypatch, tmp_path)
    assert ls.load().empty


# ── 실데이터 대조 (회귀 방지) ────────────────────────────
def test_실데이터가_KRX_캐시와_같은_값이다():
    """되짚은 주식 수 = 그 날 KRX 상장주식수. **날짜 보정을 지우면 여기서 깨진다.**

    지급기준일 당일로 찍으면 94.7% 밖에 안 맞는다. 다음 개장일이라야 전부 맞는다.
    자료 파일이 없는 환경에서는 건너뛴다(수집 결과에 의존하는 테스트라서).
    """
    import pytest

    long = ls.load()
    cache_dir = ROOT / "data" / "flow_cache"
    if long.empty or not cache_dir.exists():
        pytest.skip("분배상세 또는 수급 캐시가 없다")

    cache = {}
    for path in sorted(cache_dir.glob("etf_*.csv")):
        frame = pd.read_csv(path, dtype={"KRX코드": str})
        cache[pd.Timestamp(path.stem.replace("etf_", ""))] = dict(zip(
            frame["KRX코드"].str.strip(),
            pd.to_numeric(frame["상장주식수"], errors="coerce")))

    matched = compared = 0
    for row in long.itertuples(index=False):
        day = cache.get(row.날짜)
        if not day:
            continue
        krx = day.get(row.KRX코드)
        if krx is None or pd.isna(krx):
            continue
        compared += 1
        matched += int(krx) == row.상장주식수

    if compared < 50:
        pytest.skip(f"겹치는 구간이 {compared}건뿐이라 판정 못 함")
    assert matched == compared, f"{compared}건 중 {compared - matched}건 불일치"


def test_커버리지는_증감가능_종목을_따로_센다():
    """점이 하나뿐인 종목은 '있다'에는 들어가도 '증감가능'에는 안 들어간다."""
    두점 = _raw(["20250131", "20250430"], [100, 100],
                [100_000_000, 120_000_000], isin="KR7152100004")
    한점 = _raw(["20250131"], [100], [500_000_000], isin="KR7069500007")
    got = ls.coverage(ls.derive(pd.concat([두점, 한점], ignore_index=True)))
    assert got["종목수"] == 2
    assert got["증감가능"] == 1
    assert got["점수"] == 3
