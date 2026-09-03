# -*- coding: utf-8 -*-
"""
1단계-보조: KRX에서 ETF 기간 수익률(YTD·1년)을 전 종목 한 번에 수집
- 데이터 소스: KRX 정보데이터시스템 [13103] 전종목 등락률 (pykrx get_etf_price_change_by_ticker)
- 구간 하나당 요청 1회로 1,000여 종목이 전부 온다 → 종목별 조회 불필요
- 수익률 = 구간 시작 종가 대비 종료 종가 변화율(%). **분배금은 포함되지 않은 가격 수익률**
- 결과: data/raw/krx_etf수익률_YYYYMMDD.csv  (dashboard.py 가 KRX코드로 붙여 씀)
"""
from contextlib import redirect_stdout, redirect_stderr
from datetime import date
from io import StringIO
from pathlib import Path
import sys

import exchange_calendars as xcals
import pandas as pd

BASE = Path(__file__).resolve().parent.parent
RAW_DIR = BASE / "data" / "raw"
RAW_DIR.mkdir(parents=True, exist_ok=True)

OUTPUT_COLUMNS = ["KRX코드", "YTD수익률", "1년수익률", "시작일_YTD", "시작일_1년", "기준일자"]


def trading_bounds(today: date) -> tuple[str, str, str]:
    """(마지막 개장일, 올해 첫 개장일, 1년 전 개장일) — 모두 YYYYMMDD."""
    today_ts = pd.Timestamp(today)
    krx = xcals.get_calendar(
        "XKRX",
        start=(today_ts - pd.DateOffset(months=15)).strftime("%Y-%m-%d"),
        # 오늘이 휴장일이면 today 가 달력 범위 밖이 될 수 있어 여유를 둔다
        end=(today_ts + pd.DateOffset(days=14)).strftime("%Y-%m-%d"),
    )

    def snap_back(ts: pd.Timestamp) -> pd.Timestamp:
        """개장일이면 그대로, 아니면 직전 개장일로."""
        ts = pd.Timestamp(ts.date())
        return ts if krx.is_session(ts) else krx.date_to_session(ts, direction="previous")

    def snap_forward(ts: pd.Timestamp) -> pd.Timestamp:
        """개장일이면 그대로, 아니면 다음 개장일로 (연초 시작일용)."""
        ts = pd.Timestamp(ts.date())
        return ts if krx.is_session(ts) else krx.date_to_session(ts, direction="next")

    end = snap_back(today_ts)
    ytd_start = snap_forward(pd.Timestamp(year=today.year, month=1, day=1))
    year_start = snap_back(end - pd.DateOffset(years=1))
    return (end.strftime("%Y%m%d"), ytd_start.strftime("%Y%m%d"),
            year_start.strftime("%Y%m%d"))


def fetch_price_change(fromdate: str, todate: str) -> pd.DataFrame:
    """전 종목 구간 등락률. 티커(KRX코드) 기준으로 반환.

    ⚠ pykrx 로 **data.krx.co.kr(KDM)** 을 친다 — 약관상 자동 조회 금지 대상이다.
    수익률 갱신의 정식 경로는 `etf_refresh.py`(KRX Open API)이고, 그쪽이
    구간 시작·끝 종가로 같은 정의의 YTD·1년을 직접 계산한다.
    """
    import etf_info
    etf_info._guard_kdm()
    with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
        from pykrx import stock
        raw = stock.get_etf_price_change_by_ticker(fromdate, todate)
    if raw is None or raw.empty:
        return pd.DataFrame(columns=["KRX코드", "등락률"])
    if "등락률" not in raw.columns:
        raise ValueError("KRX 등락률 응답에 '등락률' 컬럼이 없습니다.")
    frame = pd.DataFrame({
        "KRX코드": raw.index.astype(str).str.strip(),
        "등락률": pd.to_numeric(raw["등락률"], errors="coerce"),
        "시가": pd.to_numeric(raw.get("시가"), errors="coerce"),
    })
    # 구간 시작가가 0이면 등락률이 의미 없다(그 구간에 거래가 없던 종목)
    frame = frame[frame["시가"].fillna(0) > 0]
    return frame.drop(columns="시가").dropna(subset=["등락률"])


def collect(today: date | None = None) -> pd.DataFrame:
    """YTD·1년 수익률을 KRX코드 기준으로 합친다. (요청 2회)"""
    today = today or date.today()
    end, ytd_start, year_start = trading_bounds(today)

    ytd = fetch_price_change(ytd_start, end).rename(columns={"등락률": "YTD수익률"})
    one_year = fetch_price_change(year_start, end).rename(columns={"등락률": "1년수익률"})

    merged = ytd.merge(one_year, on="KRX코드", how="outer")
    merged["시작일_YTD"] = ytd_start
    merged["시작일_1년"] = year_start
    merged["기준일자"] = end
    return merged[OUTPUT_COLUMNS].sort_values("KRX코드").reset_index(drop=True)


def latest_returns_csv() -> Path | None:
    """data/raw 에서 가장 최근 수익률 CSV. 없으면 None."""
    files = sorted(RAW_DIR.glob("krx_etf수익률_*.csv"))
    return files[-1] if files else None


def load_latest() -> pd.DataFrame:
    """저장된 수익률을 읽는다. 파일이 없으면 빈 DataFrame."""
    path = latest_returns_csv()
    if path is None:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    frame = pd.read_csv(path, dtype={"KRX코드": str, "기준일자": str,
                                     "시작일_YTD": str, "시작일_1년": str})
    for column in ("YTD수익률", "1년수익률"):
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


def save(today: date | None = None) -> Path:
    """수집해서 CSV로 저장하고 경로를 돌려준다."""
    today = today or date.today()
    df = collect(today)
    out = RAW_DIR / f"krx_etf수익률_{today.strftime('%Y%m%d')}.csv"
    df.to_csv(out, index=False, encoding="utf-8-sig")
    return out


def main():
    today = date.today()
    out = save(today)
    df = load_latest()
    print(f"저장: {out}")
    print(f"종목 수: {len(df):,}개 / 기준일: {df['기준일자'].iloc[0]} "
          f"(YTD 시작 {df['시작일_YTD'].iloc[0]} · 1년 시작 {df['시작일_1년'].iloc[0]})")
    print(f"YTD 있음: {df['YTD수익률'].notna().sum():,}개 / "
          f"1년 있음: {df['1년수익률'].notna().sum():,}개")
    print("\n[확인용 상위 5행]")
    print(df.head().to_string())


if __name__ == "__main__":
    from console import use_utf8_stdout   # 콘솔이 cp949여도 안 죽게
    use_utf8_stdout()
    from corp_ca import apply as apply_corp_ca   # 회사 HTTPS 가로채기 뒤에서도 받게
    apply_corp_ca()
    sys.exit(main())
