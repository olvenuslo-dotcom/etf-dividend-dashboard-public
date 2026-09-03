# -*- coding: utf-8 -*-
"""국내 ETF 데이터 갱신 — KRX Open API(공식) 경로.

pykrx 차단(PROGRESS 20단계) 뒤 국내 ETF 시세를 새로 받을 방법이 없었다.
ETF 일별매매 API 가 승인돼 이 스크립트가 그 자리를 잇는다:

  ① krx_etf정보_YYYYMMDD.csv — 마지막 정보 CSV 의 **정적 칸**(총보수·분류·상장일·
     운용사·ISIN…)은 그대로 두고, **시세 칸**(종가·NAV·시가총액)만 오늘 값으로 바꾼다.
     새로 상장된 종목은 API 가 주는 값만 채워서 덧붙인다.
  ② krx_etf수익률_YYYYMMDD.csv — 연초·1년 전 개장일의 종가를 받아
     YTD·1년 수익률을 직접 계산한다 (예전 [13103] 등락률 화면과 같은 정의).

실행: venv\\Scripts\\python src\\etf_refresh.py
이후: venv\\Scripts\\python src\\classify.py 로 xlsx 를 다시 만들면 대시보드에 반영된다.
"""
from datetime import date
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

BASE = Path(__file__).resolve().parent.parent
RAW_DIR = BASE / "data" / "raw"

# API 시세로 덮어쓰는 칸. 나머지는 전부 마지막 정보 CSV 값이 산다.
PRICE_COLUMNS = ["종가", "NAV", "시가총액"]


def merge_prices(old: pd.DataFrame, fresh: pd.DataFrame) -> pd.DataFrame:
    """정보 CSV(정적 칸) + 오늘 시세(API). KRX코드로 잇는다.

    old 가 비어 있으면 fresh 만으로 시작한다(분류·총보수는 빈 채 — classify 가 견딘다).
    """
    fresh = fresh.copy()
    if old is None or old.empty:
        return fresh

    old = old.copy()
    old["KRX코드"] = old["KRX코드"].astype(str).str.strip()
    fresh["KRX코드"] = fresh["KRX코드"].astype(str).str.strip()

    price = fresh.set_index("KRX코드")
    merged = old.set_index("KRX코드")
    common = merged.index.intersection(price.index)
    for column in PRICE_COLUMNS + ["기준일자"]:
        if column in price.columns:
            merged.loc[common, column] = price.loc[common, column]
    # 기초지수명이 비어 있으면 API 값으로 채운다 (KRX 화면과 같은 원천)
    if "기초지수명" in merged.columns:
        blank = merged["기초지수명"].isna() | \
            (merged["기초지수명"].astype(str).str.strip() == "")
        fill = merged.index[blank].intersection(price.index)
        merged.loc[fill, "기초지수명"] = price.loc[fill, "기초지수명"]

    # 새로 상장돼 정보 CSV 에 없는 종목 — API 가 주는 것만 채워 덧붙인다
    new_codes = price.index.difference(merged.index)
    if len(new_codes):
        addition = price.loc[new_codes, [c for c in price.columns
                                         if c in ("종목명", *PRICE_COLUMNS,
                                                  "기초지수명", "기준일자")]]
        merged = pd.concat([merged, addition])

    return merged.reset_index()


def compute_returns(now: pd.DataFrame, ytd_start: pd.DataFrame,
                    year_start: pd.DataFrame, dates: dict) -> pd.DataFrame:
    """YTD·1년 수익률 = 구간 시작 종가 대비 변화율(%). 분배금은 미포함(가격 수익률).

    dates: {"기준일자","시작일_YTD","시작일_1년"} — 실제로 받아진 날짜들.
    """
    def closes(frame: pd.DataFrame) -> pd.Series:
        if frame is None or frame.empty:
            return pd.Series(dtype="float64")
        out = frame.set_index(frame["KRX코드"].astype(str).str.strip())["종가"]
        return pd.to_numeric(out, errors="coerce")

    now_close = closes(now)
    frame = pd.DataFrame({"KRX코드": now_close.index, "종가": now_close.values})

    for label, start in (("YTD수익률", ytd_start), ("1년수익률", year_start)):
        base_close = closes(start).reindex(now_close.index)
        # 시작가가 0이거나 없으면(그 사이 상장) 수익률을 내지 않는다
        valid = base_close > 0
        frame[label] = ((now_close / base_close - 1) * 100).where(valid).round(2).values

    frame["시작일_YTD"] = dates["시작일_YTD"]
    frame["시작일_1년"] = dates["시작일_1년"]
    frame["기준일자"] = dates["기준일자"]
    return frame.drop(columns="종가").dropna(
        subset=["YTD수익률", "1년수익률"], how="all").reset_index(drop=True)


def main() -> int:
    import etf_info
    import etf_returns
    import krx_api

    today = date.today()
    end, ytd_start, year_start = etf_returns.trading_bounds(today)

    print(f"국내 ETF 갱신 (KRX Open API) — 기준일 후보 {end}")
    now = krx_api.fetch_etf_daily_with_fallback(end)
    if now.empty:
        print("시세를 받지 못했습니다. 잠시 뒤 다시 시도하세요.")
        return 1
    real_end = str(now["기준일자"].iloc[0])
    if real_end != end:
        print(f"  {end} 자료가 아직 없어 직전 개장일 {real_end} 로 대체")
    print(f"  오늘 시세 {len(now):,}종목")

    # ① 정보 CSV — 정적 칸 유지 + 시세만 교체
    old = etf_info.load_latest()
    merged = merge_prices(old, now)
    info_out = RAW_DIR / f"krx_etf정보_{real_end}.csv"
    merged.to_csv(info_out, index=False, encoding="utf-8-sig")
    updated = min(len(old), len(now)) if not old.empty else 0
    print(f"  ① {info_out.name} — {len(merged):,}종목 "
          f"(시세 갱신 {updated:,} / 신규 {len(merged) - len(old):,})")

    # ② 수익률 CSV — 연초·1년 전 종가에서 직접 계산
    ytd_frame = krx_api.fetch_etf_daily_with_fallback(ytd_start)
    year_frame = krx_api.fetch_etf_daily_with_fallback(year_start)
    returns = compute_returns(now, ytd_frame, year_frame, {
        "기준일자": real_end,
        "시작일_YTD": str(ytd_frame["기준일자"].iloc[0]) if not ytd_frame.empty else ytd_start,
        "시작일_1년": str(year_frame["기준일자"].iloc[0]) if not year_frame.empty else year_start,
    })
    returns_out = RAW_DIR / f"krx_etf수익률_{real_end}.csv"
    returns.to_csv(returns_out, index=False, encoding="utf-8-sig")
    print(f"  ② {returns_out.name} — YTD {returns['YTD수익률'].notna().sum():,}개 / "
          f"1년 {returns['1년수익률'].notna().sum():,}개")

    print("\n다음: venv\\Scripts\\python src\\classify.py 로 목록을 다시 만드세요.")
    return 0


if __name__ == "__main__":
    from console import use_utf8_stdout   # 콘솔이 cp949여도 안 죽게
    use_utf8_stdout()
    from corp_ca import apply as apply_corp_ca   # 회사 HTTPS 가로채기 뒤에서도 받게
    apply_corp_ca()
    sys.exit(main())
