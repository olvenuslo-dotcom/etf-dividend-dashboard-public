# -*- coding: utf-8 -*-
"""장기 상장주식수 — **분배 이력에서 되짚어 낸다. 추가 수집 0.**

날 단위 캐시(fetch.py)는 KRX Open API 를 하루에 한 번씩 받아 쌓는 것이라
**쌓기 시작한 날부터**만 있다(지금 60일치). 그런데 설정·환매는 몇 달 단위로
흐름이 갈리는 값이라 60일로는 방향이 안 보인다.

세이브로 `펀드별분배금지급내역`(fund_dist.py)이 주는 **총분배금**으로 그 구간을
메운다. 총분배금은 종목 전체에 지급한 금액이므로

    총분배금 ÷ 주당분배금 = 그 분배를 받을 권리가 있던 주식 수

가 된다. 분배가 있던 날마다 점이 하나씩 생겨 **2년치**가 공짜로 나온다.

### 날짜를 하루 미뤄 붙이는 이유 (실측)
캐시와 겹치는 826 건을 대조했더니 **지급기준일 당일** 은 782/826(94.7%)만 맞고
**기준일 다음 거래일** 은 826/826(100%)이 맞았다. 기준일 마감 기준 주주명부가
KRX 상장주식수에 다음 세션으로 반영되기 때문이다. 그래서 여기서도 다음 거래일에
찍는다 — 날 단위 캐시와 한 차트에 겹쳐 그려도 어긋나지 않는다.

한계 (화면에도 적는다):
- **점이 분배일에만 있다.** 연 1회 분배 종목은 2년에 점 두 개뿐이라 추세를 못 본다.
- 분배 이력이 없는 종목(미분배·신규상장)은 아예 안 나온다.
- 국내 전용이다. 미국 종목은 세이브로 자료가 아니다.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parent.parent.parent
RAW_DIR = BASE / "data" / "raw"
# 계산 결과를 넣어 두는 곳. 원본에서 언제든 다시 만들 수 있어 gitignore 대상이다.
CACHE_DIR = BASE / "data" / "flow_cache"

MIN_POINTS = 2      # 증감을 말하려면 점이 최소 둘은 있어야 한다

COLUMNS = ["KRX코드", "ISIN", "종목명", "날짜", "지급기준일", "상장주식수"]


def latest_detail_csv() -> Path | None:
    """가장 최근 분배상세 CSV (fund_dist.py 산출물)."""
    files = sorted(RAW_DIR.glob("seibro_분배상세_*.csv"))
    return files[-1] if files else None


def _next_sessions(days: pd.Series) -> pd.Series:
    """각 날짜의 **다음 개장일**. XKRX 로 잡는다 (fetch.sessions_back 과 같은 달력)."""
    import exchange_calendars as xcals

    valid = days.dropna()
    if valid.empty:
        return pd.Series(pd.NaT, index=days.index, dtype="datetime64[ns]")
    krx = xcals.get_calendar(
        "XKRX",
        start=(valid.min() - pd.Timedelta(days=10)).strftime("%Y-%m-%d"),
        end=(valid.max() + pd.Timedelta(days=20)).strftime("%Y-%m-%d"))
    sessions = pd.DatetimeIndex(krx.sessions).tz_localize(None)
    # searchsorted(right) → 그 날짜보다 '뒤' 인 첫 개장일
    position = sessions.searchsorted(valid.values, side="right")
    mapped = pd.Series(pd.NaT, index=days.index, dtype="datetime64[ns]")
    inside = position < len(sessions)
    mapped.loc[valid.index[inside]] = sessions[position[inside]]
    return mapped


def derive(detail: pd.DataFrame) -> pd.DataFrame:
    """분배상세 → (KRX코드, 종목명, 날짜, 상장주식수) 장기 시계열.

    청산분배는 뺀다(다른 모듈과 같은 규칙 — 청산은 분배가 아니라 정리다).
    """
    columns = COLUMNS
    if detail is None or detail.empty:
        return pd.DataFrame(columns=columns)

    frame = detail.copy()
    if "배당구분" in frame.columns:
        frame = frame[frame["배당구분"] != "청산분배"]

    per_share = pd.to_numeric(frame.get("주당분배금"), errors="coerce")
    total = pd.to_numeric(frame.get("총분배금"), errors="coerce")
    # 주당분배금이 0 이면 나눌 수 없다. 총분배금이 비어도 마찬가지.
    usable = (per_share > 0) & (total > 0)
    frame = frame[usable].copy()
    if frame.empty:
        return pd.DataFrame(columns=columns)

    frame["상장주식수"] = (total[usable] / per_share[usable]).round().astype("int64")
    frame["지급기준일"] = pd.to_datetime(frame["지급기준일"], format="%Y%m%d",
                                        errors="coerce")
    frame = frame[frame["지급기준일"].notna()]
    if frame.empty:
        return pd.DataFrame(columns=columns)

    frame["날짜"] = _next_sessions(frame["지급기준일"])
    frame["KRX코드"] = frame["ISIN"].astype(str).str[3:9]
    # 같은 종목·같은 기준일이 두 번 오면 하나만 (재공시 등)
    frame = frame.drop_duplicates(["ISIN", "지급기준일"])
    return (frame[columns].sort_values(["KRX코드", "날짜"])
            .reset_index(drop=True))


def _cache_path(source: Path) -> Path:
    """원본 파일마다 캐시가 따로. 새 수집분이 오면 자연히 새로 만든다."""
    return CACHE_DIR / f"장기주식수_{source.stem}.csv"


def load(use_cache: bool = True) -> pd.DataFrame:
    """장기 시계열. **결과를 디스크에 캐시한다.**

    왜 캐시하나: `derive()` 가 `exchange_calendars` 로 XKRX 달력을 짓는데 이게
    **23초** 걸린다. 대시보드 첫 화면에 그대로 얹히면 못 쓴다. 결과는 5,449줄짜리
    작은 표라 CSV 로 두면 0.3초에 읽힌다.

    캐시는 **원본보다 오래됐으면 버린다**(원본을 고쳐도 낡은 값이 안 남게).
    """
    source = latest_detail_csv()
    if source is None:
        return pd.DataFrame(columns=COLUMNS)

    cache = _cache_path(source)
    if use_cache and cache.exists() \
            and cache.stat().st_mtime >= source.stat().st_mtime:
        frame = pd.read_csv(cache, dtype={"KRX코드": str, "ISIN": str},
                            parse_dates=["날짜", "지급기준일"])
        return frame.reindex(columns=COLUMNS)

    frame = derive(pd.read_csv(source, dtype=str))
    if use_cache and not frame.empty:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        frame.to_csv(cache, index=False, encoding="utf-8-sig")
    return frame


def series(long: pd.DataFrame, code: str) -> pd.DataFrame:
    """한 종목의 (index=날짜) 상장주식수. 없으면 빈 표."""
    if long is None or long.empty:
        return pd.DataFrame()
    hit = long[long["KRX코드"].astype(str).str.strip() == str(code).strip()]
    if hit.empty:
        return pd.DataFrame()
    return (hit.set_index("날짜")[["상장주식수"]].sort_index())


def growth(long: pd.DataFrame, code: str, months: int | None = None) -> dict:
    """한 종목의 장기 증감.

    months 를 주면 **마지막 점에서 그만큼 거슬러 올라간 뒤**의 점들만 본다.
    (오늘 기준이 아니라 마지막 점 기준이다 — 분배가 끊긴 종목의 창이
     통째로 비어 '증감 0' 으로 보이는 것을 막는다.)
    """
    empty = {"시작": None, "끝": None, "증감": None, "증감률": None,
             "점개수": 0, "시작일": None, "끝일": None}
    frame = series(long, code)
    if frame.empty:
        return empty
    values = frame["상장주식수"].dropna()
    if months:
        cutoff = values.index.max() - pd.DateOffset(months=months)
        values = values[values.index >= cutoff]
    if len(values) < MIN_POINTS:
        return {**empty, "점개수": int(len(values)),
                "시작": int(values.iloc[0]) if len(values) else None,
                "끝": int(values.iloc[-1]) if len(values) else None,
                "시작일": values.index[0] if len(values) else None,
                "끝일": values.index[-1] if len(values) else None}
    first, last = int(values.iloc[0]), int(values.iloc[-1])
    return {
        "시작": first, "끝": last,
        "증감": last - first,
        "증감률": (last / first - 1) * 100 if first > 0 else None,
        "점개수": int(len(values)),
        "시작일": values.index[0], "끝일": values.index[-1],
    }


def coverage(long: pd.DataFrame) -> dict:
    """이 자료가 몇 종목·어느 기간을 덮는가 (화면에 정직하게 적기 위한 값)."""
    if long is None or long.empty:
        return {"종목수": 0, "점수": 0, "증감가능": 0, "시작": None, "끝": None}
    per_code = long.groupby("KRX코드").size()
    return {
        "종목수": int(per_code.size),
        "점수": int(len(long)),
        "증감가능": int((per_code >= MIN_POINTS).sum()),
        "시작": long["날짜"].min(),
        "끝": long["날짜"].max(),
    }
