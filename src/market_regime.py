# -*- coding: utf-8 -*-
"""시장국면 데이터 준비 — 순수 계산. 대시보드의 [시장국면] 탭이 쓴다.

그래프 두 장을 위한 표를 만든다:
  ① DRAM 현물가 ↔ 국고채 10년 금리 (일별)
  ② 한국 수출 ↔ 국고채 10년 금리 (월별)

자료 출처가 셋이라 여기서 합친다:
  · 블룸버그 수기 추출(data/reference/블룸버그_시계열.csv)
      — DDR4 8Gb 현물, NAND 512Gb, 국고채 10년. 2023-10 부터의 **과거 구간**
  · TrendForce 매일 수집(ddr5.py) — DDR5 현물. **오늘부터 쌓이는** 구간
  · ECOS(ecos.py) — 국고채 10년(일별)·수출금액(월별). 갱신용

⚠ 상관은 인과가 아니다. 둘 다 같은 것(AI 투자 사이클 → 수출 호조 → 성장·물가
   기대)에 반응한 결과일 수 있다. 화면에도 이 주의를 적는다.
"""
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))   # ecos·ddr5 를 찾게

BASE = Path(__file__).resolve().parent.parent
BLOOMBERG_CSV = BASE / "data" / "reference" / "블룸버그_시계열.csv"


def load_bloomberg(path: Path | None = None) -> pd.DataFrame:
    """블룸버그 수기 추출분. 없으면 빈 표(탭은 있는 것만 그린다)."""
    path = path or BLOOMBERG_CSV
    if not path.exists():
        return pd.DataFrame(columns=["날짜", "DRAM_DDR4_8Gb", "NAND_512Gb", "국고채10년"])
    frame = pd.read_csv(path, parse_dates=["날짜"])
    for column in frame.columns:
        if column != "날짜":
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.dropna(subset=["날짜"]).sort_values("날짜").reset_index(drop=True)


def rate_series() -> pd.DataFrame:
    """국고채 10년 일별 시계열 (날짜, 금리).

    블룸버그 구간을 그대로 쓰고, 그 **마지막 날짜 이후**만 ECOS 로 잇는다.
    같은 날 값이 미세하게 다를 수 있어(호가 시점 차이) 한 출처 안에서는 섞지 않는다.
    """
    bloomberg = load_bloomberg()
    parts = []
    if not bloomberg.empty and "국고채10년" in bloomberg.columns:
        parts.append(bloomberg[["날짜", "국고채10년"]].rename(columns={"국고채10년": "금리"}))

    try:
        import ecos
        bok = ecos.series("국고채10년").rename(columns={"값": "금리"})
    except Exception:
        bok = pd.DataFrame(columns=["날짜", "금리"])
    if not bok.empty:
        cutoff = parts[0]["날짜"].max() if parts else pd.Timestamp.min
        parts.append(bok[bok["날짜"] > cutoff])

    if not parts:
        return pd.DataFrame(columns=["날짜", "금리"])
    merged = pd.concat(parts, ignore_index=True)
    return (merged.dropna(subset=["금리"]).drop_duplicates("날짜", keep="first")
            .sort_values("날짜").reset_index(drop=True))


def dram_vs_rate() -> pd.DataFrame:
    """① 그래프용 (날짜, DRAM_DDR4, DDR5, 금리).

    DDR4 8Gb(블룸버그 과거분)와 DDR5 16Gb(TrendForce 누적분)는 **다른 물건**이라
    한 줄로 잇지 않고 두 열로 나란히 둔다. 값대가 비슷해도(49 vs 51) 별개 선이다.
    """
    out = load_bloomberg()[["날짜", "DRAM_DDR4_8Gb"]] if BLOOMBERG_CSV.exists() \
        else pd.DataFrame(columns=["날짜", "DRAM_DDR4_8Gb"])
    out = out.rename(columns={"DRAM_DDR4_8Gb": "DRAM_DDR4"})

    try:
        import ddr5
        spot = ddr5.series().rename(columns={"평균가": "DDR5"})
    except Exception:
        spot = pd.DataFrame(columns=["날짜", "DDR5"])

    rate = rate_series()
    merged = out.merge(spot, on="날짜", how="outer").merge(rate, on="날짜", how="outer")
    merged = merged.sort_values("날짜").reset_index(drop=True)
    # 값이 하나도 없는 날(휴장 등)은 버린다
    value_columns = [c for c in ("DRAM_DDR4", "DDR5", "금리") if c in merged.columns]
    return merged.dropna(subset=value_columns, how="all").reset_index(drop=True)


def exports_vs_rate() -> pd.DataFrame:
    """② 그래프용 (월, 수출금액, 금리). 월별이라 금리는 **월평균**으로 맞춘다."""
    try:
        import ecos
        exports = ecos.series("수출금액").rename(columns={"값": "수출금액"})
    except Exception:
        exports = pd.DataFrame(columns=["날짜", "수출금액"])
    if exports.empty:
        return pd.DataFrame(columns=["월", "수출금액", "금리"])

    exports = exports.copy()
    exports["월"] = exports["날짜"].dt.to_period("M")

    rate = rate_series()
    if rate.empty:
        monthly_rate = pd.DataFrame(columns=["월", "금리"])
    else:
        rate = rate.copy()
        rate["월"] = rate["날짜"].dt.to_period("M")
        monthly_rate = rate.groupby("월", as_index=False)["금리"].mean()

    merged = exports[["월", "수출금액"]].merge(monthly_rate, on="월", how="left")
    merged["월"] = merged["월"].dt.to_timestamp()      # 차트 축은 datetime 이 편하다
    return merged.sort_values("월").reset_index(drop=True)


# 예탁금·예탁금/시총 이동평균 창(개월) — 짧은 창은 최근 방향, 긴 창은 큰 추세
MA_WINDOWS = (3, 6, 12)


def deposits() -> pd.DataFrame:
    """③ 그래프용 투자자예탁금 (월, 예탁금_조원, 평균_조원).

    ECOS 원 단위를 조원으로 바꾼다. '평균' 열은 **지금 쌓인 구간 전체**의 평균이라,
    이력을 3년 → 5년 → 10년으로 늘릴수록 '역사적 평균'에 가까워진다.
    """
    try:
        import ecos
        raw = ecos.series("투자자예탁금")
    except Exception:
        raw = pd.DataFrame(columns=["날짜", "값"])
    if raw.empty:
        return pd.DataFrame(columns=["월", "예탁금_조원", "평균_조원"])

    out = pd.DataFrame({
        "월": raw["날짜"],
        "예탁금_조원": raw["값"] / 1e12,      # 원 → 조원
    })
    out = out.sort_values("월").reset_index(drop=True)
    out["평균_조원"] = out["예탁금_조원"].mean()
    # 이동평균 — 수평선(역사적 평균)은 '지금이 높나 낮나'를, 이동평균은
    # '최근 추세가 어느 쪽인가'를 보여준다. 창이 안 쌓인 앞부분은 비운다
    for window in MA_WINDOWS:
        out[f"이동평균{window}_조원"] = out["예탁금_조원"].rolling(window).mean()
    return out


def deposit_ratio() -> pd.DataFrame:
    """③-2 그래프용 예탁금/시총 비중 (월, 비중, 평균, 분모시장).

    분모는 **KOSPI+KOSDAQ 합산**이다. 코스닥 시총이 없는 달은 분모가 달라져
    추이가 왜곡되므로 **버린다**(두 시장이 다 있는 달만 계산). 코스닥 이력이
    아예 없으면 KOSPI 만으로 내고 `분모시장` 칸에 표시한다.
    """
    dep = deposits()
    try:
        import krx_api
        kospi = krx_api.load("KOSPI")
        kosdaq = krx_api.load("KOSDAQ")
    except Exception:
        kospi = kosdaq = pd.DataFrame(columns=["월", "시총_조원"])
    if dep.empty or kospi.empty:
        return pd.DataFrame(columns=["월", "비중", "평균", "분모시장"])

    caps = kospi[["월", "시총_조원"]]
    label = "KOSPI"
    if not kosdaq.empty:
        caps = caps.merge(kosdaq[["월", "시총_조원"]], on="월",
                          how="inner", suffixes=("", "_ksq"))
        caps["시총_조원"] = caps["시총_조원"] + caps["시총_조원_ksq"]
        caps = caps[["월", "시총_조원"]]
        label = "KOSPI+KOSDAQ"

    dep = dep.copy()
    dep["_월키"] = dep["월"].dt.strftime("%Y%m")
    merged = dep.merge(caps.rename(columns={"월": "_월키"}), on="_월키", how="inner")
    if merged.empty:
        return pd.DataFrame(columns=["월", "비중", "평균", "분모시장"])
    merged["비중"] = (merged["예탁금_조원"] / merged["시총_조원"] * 100).round(2)
    merged = merged.sort_values("월").reset_index(drop=True)
    merged["평균"] = merged["비중"].mean()
    for window in MA_WINDOWS:                       # deposits() 와 같은 창
        merged[f"이동평균{window}"] = merged["비중"].rolling(window).mean()
    merged["분모시장"] = label
    return merged[["월", "비중", "평균",
                   *[f"이동평균{w}" for w in MA_WINDOWS], "분모시장"]]


def correlation(frame: pd.DataFrame, left: str, right: str) -> float | None:
    """두 열의 상관계수. 겹치는 표본이 10개 미만이면 내지 않는다(무의미하다)."""
    if left not in frame.columns or right not in frame.columns:
        return None
    paired = frame[[left, right]].dropna()
    if len(paired) < 10:
        return None
    return round(float(paired[left].corr(paired[right])), 3)
