# -*- coding: utf-8 -*-
"""
13단계: 미국 직접 상장 ETF 수집 (SCHD·JEPI·QYLD …)

데이터 소스 (둘 다 무료, API 키 없음):
  * 종목 목록  ← nasdaqtrader.com 공개 심볼 파일 (ETF 플래그가 들어 있다)
  * 배당·시세 ← yfinance (야후 파이낸스). 배치로 한 번에 수백 개씩 받는다
  * 환율      ← yfinance 'KRW=X' (원/달러)

국내 ETF 표(분배ETF목록.xlsx)와 **같은 칸 이름**으로 맞춰서 내보낸다.
금액 칸(시가총액·주당분배금·종가)은 **원화로 환산**해서 넣는다 — 한 표에서 비교하려면
통화가 같아야 한다. 환산에 쓴 환율과 기준일은 CSV 의 '환율'·'기준일자' 칸에 남긴다.

결과: data/raw/us_etf_YYYYMMDD.csv
실행: venv\\Scripts\\python src\\us_etf.py
"""
from datetime import date, datetime, timezone
from pathlib import Path
import io
import sys
import time

import pandas as pd

BASE = Path(__file__).resolve().parent.parent
RAW_DIR = BASE / "data" / "raw"
RAW_DIR.mkdir(parents=True, exist_ok=True)

SYMBOL_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/"
SYMBOL_FILES = [("nasdaqlisted.txt", "Symbol", "NASDAQ"),
                ("otherlisted.txt", "ACT Symbol", "NYSE/AMEX")]

BATCH = 500           # yfinance 한 번에 받을 종목 수
# 분배주기 판정은 최근 12개월이지만(CLAUDE.md 규칙), 2년치를 받아야
# 1년 수익률과 배당 성장률(직전 12개월 vs 그 앞 12개월)을 낼 수 있다
HISTORY_PERIOD = "2y"

OUTPUT_COLUMNS = [
    "티커", "종목명", "거래소", "운용사", "통화",
    "시가총액", "총보수", "종가",
    "지급횟수_12개월", "중앙값간격_일", "최근_주당분배금", "최근_분배율",
    "연간주당분배금", "연간분배율", "분배금성장률",
    "YTD수익률", "1년수익률", "3년평균수익률", "5년평균수익률",
    "변동성", "최대낙폭", "베타",
    "설정일", "NAV", "괴리율",
    "최근_지급기준일", "일평균거래대금", "카테고리", "환율", "기준일자",
]


def fetch_symbols() -> pd.DataFrame:
    """nasdaqtrader 심볼 파일에서 ETF만 골라 (티커, 종목명, 거래소)."""
    import requests

    frames = []
    for name, symbol_column, market in SYMBOL_FILES:
        text = requests.get(SYMBOL_URL + name, timeout=60).text
        raw = pd.read_csv(io.StringIO(text), sep="|")
        # 마지막 줄은 'File Creation Time' 안내라 ETF 플래그가 비어 있다 → 자동으로 걸러진다
        raw = raw[(raw.get("ETF") == "Y") & (raw.get("Test Issue") == "N")]
        frames.append(pd.DataFrame({
            "티커": raw[symbol_column].astype(str).str.strip(),
            "종목명": raw["Security Name"].astype(str).str.strip(),
            "거래소": market,
        }))
    out = pd.concat(frames, ignore_index=True)
    # 우선주·워런트 표기가 섞인 티커(BRK.A 형태)는 yfinance 조회가 안 된다
    out = out[out["티커"].str.match(r"^[A-Z]{1,5}$", na=False)]
    return out.drop_duplicates("티커").reset_index(drop=True)


def fetch_usdkrw() -> float:
    """원/달러 환율. 못 받으면 0을 돌려준다(환산 없이 USD 그대로 둔다)."""
    import yfinance as yf

    try:
        rate = yf.download("KRW=X", period="5d", progress=False,
                           auto_adjust=False)["Close"].dropna()
        return float(rate.iloc[-1].item()) if len(rate) else 0.0
    except Exception:
        return 0.0


def _pct(numerator: float, denominator: float, digits: int = 2) -> float | None:
    """비율(%) — 분모가 0이거나 값이 없으면 None."""
    if not denominator or pd.isna(numerator) or pd.isna(denominator):
        return None
    return round(float(numerator) / float(denominator) * 100, digits)


def summarize_one(frame: pd.DataFrame) -> dict | None:
    """한 종목의 시세·배당에서 필요한 값만 뽑는다 (2년치를 받아 1년 지표를 낸다)."""
    if frame is None or frame.empty:
        return None
    close = frame["Close"].dropna()
    if close.empty:
        return None

    dividends = frame["Dividends"] if "Dividends" in frame.columns else pd.Series(dtype=float)
    dividends = dividends[dividends > 0]
    last_close = float(close.iloc[-1])
    last_day = close.index[-1]

    # 최근 12개월 / 그 앞 12개월로 잘라 분배주기·성장률을 본다
    year_ago = last_day - pd.DateOffset(years=1)
    two_years_ago = last_day - pd.DateOffset(years=2)
    recent = dividends[dividends.index > year_ago]
    previous = dividends[(dividends.index > two_years_ago) & (dividends.index <= year_ago)]

    gaps = recent.index.to_series().diff().dropna().dt.days
    volume = frame["Volume"].fillna(0) if "Volume" in frame.columns else pd.Series(0.0)
    recent_close = close[close.index > year_ago]

    # YTD: 올해 첫 거래일 대비. 연초 데이터가 없으면(신규 상장) None
    this_year = close[close.index >= pd.Timestamp(last_day.year, 1, 1)]
    year_start = float(this_year.iloc[0]) if len(this_year) > 1 else None
    # 1년 전 종가 — 그 날짜 이하의 마지막 값
    older = close[close.index <= year_ago]
    price_year_ago = float(older.iloc[-1]) if len(older) else None

    ttm = float(recent.sum())
    daily_return = recent_close.pct_change().dropna()
    running_max = recent_close.cummax()

    return {
        "종가": last_close,
        "지급횟수_12개월": int(len(recent)),
        "중앙값간격_일": round(float(gaps.median()), 1) if len(gaps) else None,
        "최근_주당분배금": float(recent.iloc[-1]) if len(recent) else None,
        # 분배율(%) = 직전 회차 분배금 ÷ 현재가 — 통화가 달라도 그대로 비교된다
        "최근_분배율": _pct(recent.iloc[-1], last_close, 4) if len(recent) else None,
        # 연간(TTM) — 주기가 다른 종목끼리 비교하려면 이쪽을 봐야 한다
        "연간주당분배금": ttm if len(recent) else None,
        "연간분배율": _pct(ttm, last_close) if len(recent) else None,
        # 직전 12개월 분배금이 그 앞 12개월보다 얼마나 늘었나
        "분배금성장률": (_pct(ttm - previous.sum(), previous.sum())
                         if len(previous) and previous.sum() else None),
        "YTD수익률": _pct(last_close - year_start, year_start) if year_start else None,
        "1년수익률": (_pct(last_close - price_year_ago, price_year_ago)
                      if price_year_ago else None),
        # 연율화 변동성(일간 표준편차 × √252)
        "변동성": (round(float(daily_return.std()) * (252 ** 0.5) * 100, 2)
                   if len(daily_return) > 30 else None),
        "최대낙폭": (round(float(((recent_close - running_max) / running_max).min()) * 100, 2)
                     if len(recent_close) > 1 else None),
        "최근_지급기준일": recent.index[-1].date() if len(recent) else None,
        "일평균거래대금": float(
            (recent_close * volume.reindex(recent_close.index).fillna(0)).mean()),
    }


def dividend_rows(ticker: str, frame: pd.DataFrame) -> list[dict]:
    """배당 이력을 한 줄씩 (포트폴리오의 '받은 분배금' 계산에 쓴다)."""
    if frame is None or frame.empty or "Dividends" not in frame.columns:
        return []
    paid = frame["Dividends"]
    paid = paid[paid > 0]
    # 야후가 주는 날짜는 배당락일(ex-date)이다 — 국내의 '지급기준일'과 뜻이 다르다
    return [{"티커": ticker, "배당락일": day.date(), "주당분배금": float(amount)}
            for day, amount in paid.items()]


def fetch_history(tickers: list[str], batch: int = BATCH) -> tuple[pd.DataFrame, pd.DataFrame]:
    """배치로 1년치 시세·배당을 받아 (종목별 요약, 배당 이력) 두 표로 만든다."""
    import yfinance as yf

    rows, history = [], []
    for start in range(0, len(tickers), batch):
        chunk = tickers[start:start + batch]
        data = yf.download(chunk, period=HISTORY_PERIOD, actions=True,
                           group_by="ticker", progress=False, threads=True,
                           auto_adjust=False)
        for ticker in chunk:
            try:
                one = data[ticker]
            except (KeyError, TypeError):
                continue
            summary = summarize_one(one)
            if summary:
                rows.append({"티커": ticker, **summary})
                history += dividend_rows(ticker, one)
        done = min(start + batch, len(tickers))
        print(f"  시세·배당 {done:,}/{len(tickers):,}개", flush=True)
    return pd.DataFrame(rows), pd.DataFrame(history,
                                            columns=["티커", "배당락일", "주당분배금"])


# fill_details 가 다시 채워 주는 숫자 칸들 (설정일은 글자라 따로 다룬다)
FILLABLE = ("총보수", "시가총액", "NAV", "베타", "3년평균수익률", "5년평균수익률")
# 그중 달러로 와서 원화 환산이 필요한 칸
MONEY_COLUMNS = ("시가총액", "NAV")


def _blank(series: pd.Series) -> pd.Series:
    """글자 칸이 비었는지 (NaN·빈칸·'nan' 을 모두 빈 것으로 본다)."""
    return series.isna() | series.astype(str).str.strip().isin(("", "nan", "None"))


def premium_discount(price, nav) -> pd.Series:
    """괴리율(%) = (종가 - NAV) / NAV × 100.

    \\+ 면 NAV보다 비싸게(프리미엄), - 면 싸게(디스카운트) 거래된다는 뜻.
    거래가 뜸한 ETF는 이 값이 크게 벌어져, 사고팔 때 그만큼 손해를 볼 수 있다.
    둘 다 **같은 통화**여야 한다.
    """
    price = pd.to_numeric(price, errors="coerce")
    nav = pd.to_numeric(nav, errors="coerce")
    # NAV가 0이면 나눌 수 없다 (0으로 나누면 inf 가 나와 표가 깨진다)
    return ((price - nav) / nav.where(nav > 0) * 100).round(2)


def _epoch_to_date(value) -> str:
    """야후가 주는 유닉스 초 → 'YYYY-MM-DD'. 없으면 빈 글자."""
    if not isinstance(value, (int, float)) or value <= 0:
        return ""
    try:
        # 설정일은 날짜만 쓰므로 UTC 기준으로 충분하다
        return datetime.fromtimestamp(value, tz=timezone.utc).date().isoformat()
    except (OverflowError, OSError, ValueError):
        return ""


def _to_percent(value):
    """0.1497 처럼 비율로 오는 수익률을 % 로. 이미 % 면 그대로 둔다."""
    if not isinstance(value, (int, float)):
        return None
    # 연평균 수익률이 ±1.5(=150%)를 넘는 ETF는 사실상 없다 → 그 안이면 비율로 본다
    return value * 100 if -1.5 < value < 1.5 else value


def fetch_details(tickers: list[str], workers: int = 4) -> pd.DataFrame:
    """총보수·운용사·순자산은 종목마다 따로 물어봐야 한다 — 스레드로 나눠 받는다.

    ⚠ 야후는 이 경로(quoteSummary)에 **호출 제한**이 세다. 스레드를 12개로 돌렸더니
    전부 401(Invalid Crumb) 로 막혔다. 4개 정도로 낮추고, 막히면 `fill_details()` 로
    나중에 다시 채우는 쪽이 낫다. 배당·시세는 이 제한과 무관하다(다른 경로).
    """
    from concurrent.futures import ThreadPoolExecutor
    import yfinance as yf

    # 실패 사유를 종류별로 세어 둔다. 예전엔 조용히 삼켜서, TLS 가로채기로 전부
    # 막혔을 때도 밖에서는 "0개 채움" 으로만 보였다(2026-08-07).
    errors: dict[str, int] = {}

    def one(ticker: str) -> dict:
        try:
            info = yf.Ticker(ticker).get_info()
        except Exception as exc:
            errors[type(exc).__name__] = errors.get(type(exc).__name__, 0) + 1
            return {"티커": ticker}
        fee = info.get("netExpenseRatio")
        if fee is None:
            fee = info.get("annualReportExpenseRatio")
            # 옛 필드는 0.0006 처럼 비율로 온다 → % 로 맞춘다
            fee = fee * 100 if isinstance(fee, (int, float)) and fee < 0.5 else fee
        return {
            "티커": ticker,
            "운용사": info.get("fundFamily") or "",
            "총보수": fee,
            "시가총액": info.get("totalAssets"),
            "카테고리": info.get("category") or "",
            "통화": info.get("currency") or "USD",
            # ↓ 같은 응답에 이미 들어 있는 값들. 추가 호출이 없으니 공짜다.
            "설정일": _epoch_to_date(info.get("fundInceptionDate")),
            "NAV": info.get("navPrice"),
            "베타": info.get("beta3Year"),
            # 야후는 3·5년 평균을 비율(0.1497)로 준다 → % 로 맞춘다
            "3년평균수익률": _to_percent(info.get("threeYearAverageReturn")),
            "5년평균수익률": _to_percent(info.get("fiveYearAverageReturn")),
        }

    rows, t0 = [], time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for i, row in enumerate(pool.map(one, tickers), start=1):
            rows.append(row)
            if i % 200 == 0 or i == len(tickers):
                print(f"  상세 {i:,}/{len(tickers):,}개 "
                      f"({time.perf_counter() - t0:.0f}초)", flush=True)

    if errors:
        total = sum(errors.values())
        detail = " / ".join(f"{name} {n:,}건" for name, n in
                            sorted(errors.items(), key=lambda kv: -kv[1]))
        print(f"  ※ {total:,}/{len(tickers):,}개가 실패했습니다 — {detail}", flush=True)
        if total == len(tickers):
            # 전부 실패면 기다렸다 재시도해도 소용없다. 원인을 짚어 준다.
            print("  전부 실패했습니다. 호출 제한이 아니라 환경 문제일 수 있습니다"
                  " (CertificateVerifyError 면 HTTPS 가로채기 → CURL_CA_BUNDLE 확인).",
                  flush=True)
    return pd.DataFrame(rows)


def collect(min_turnover: float = 100_000.0) -> tuple[pd.DataFrame, pd.DataFrame]:
    """미국 상장 ETF 중 **최근 1년 배당이 있고 거래가 되는** 종목만 모은다.

    min_turnover: 일평균 거래대금(달러) 하한. 너무 안 팔리는 종목을 걸러낸다.
    돌려주는 값: (종목 요약표, 배당 이력표)
    """
    symbols = fetch_symbols()
    print(f"미국 상장 ETF 목록: {len(symbols):,}개")

    history, dividends = fetch_history(list(symbols["티커"]))
    print(f"시세를 받은 종목: {len(history):,}개 / 배당 이력 {len(dividends):,}건")

    keep = history[(history["지급횟수_12개월"] > 0)
                   & (history["일평균거래대금"] >= min_turnover)]
    print(f"배당 있고 거래대금 ${min_turnover:,.0f} 이상: {len(keep):,}개")

    details = fetch_details(list(keep["티커"]))
    merged = (keep.merge(symbols, on="티커", how="left")
              .merge(details, on="티커", how="left"))

    # 괴리율은 환산 전 같은 통화(USD)끼리 계산한다. 비율이라 환산해도 값은 같지만,
    # 한쪽만 환산된 상태에서 계산하는 실수를 막으려고 여기서 먼저 낸다.
    merged["괴리율"] = premium_discount(merged["종가"], merged["NAV"])

    rate = fetch_usdkrw()
    print(f"원/달러 환율: {rate:,.2f}" if rate else "※ 환율을 못 받아 USD 그대로 둡니다")
    if rate:
        # 금액 칸만 환산한다. 비율(%) 칸은 통화와 무관하니 손대지 않는다
        for column in ("시가총액", "종가", "최근_주당분배금", "연간주당분배금", "NAV"):
            merged[column] = pd.to_numeric(merged[column], errors="coerce") * rate

    merged["환율"] = rate or None
    merged["기준일자"] = date.today().strftime("%Y%m%d")
    for column in OUTPUT_COLUMNS:
        if column not in merged.columns:
            merged[column] = None
    merged = merged[OUTPUT_COLUMNS].sort_values(
        "시가총액", ascending=False, na_position="last").reset_index(drop=True)

    # 배당 이력도 남긴 종목만 남긴다. 금액은 종목 요약과 같은 환율로 원화 환산
    dividends = dividends[dividends["티커"].isin(set(merged["티커"]))].copy()
    if rate:
        dividends["주당분배금"] = dividends["주당분배금"] * rate
    return merged, dividends


def latest_csv() -> Path | None:
    files = sorted(RAW_DIR.glob("us_etf_*.csv"))
    return files[-1] if files else None


def load_dividends() -> pd.DataFrame:
    """미국 ETF 배당 이력 (티커, 배당락일, 주당분배금 — 원화 환산). 없으면 빈 표."""
    files = sorted(RAW_DIR.glob("us_배당_*.csv"))
    if not files:
        return pd.DataFrame(columns=["티커", "배당락일", "주당분배금"])
    frame = pd.read_csv(files[-1], dtype={"티커": str})
    frame["배당락일"] = pd.to_datetime(frame["배당락일"], errors="coerce")
    frame["주당분배금"] = pd.to_numeric(frame["주당분배금"], errors="coerce")
    return frame.dropna(subset=["배당락일", "주당분배금"])


def load_latest() -> pd.DataFrame:
    """저장된 미국 ETF CSV. 없으면 빈 표(대시보드는 미국 종목 없이 그대로 돈다)."""
    path = latest_csv()
    if path is None:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    frame = pd.read_csv(path, dtype={"티커": str})
    for column in ("시가총액", "총보수", "종가", "최근_주당분배금", "최근_분배율",
                   "연간주당분배금", "연간분배율", "분배금성장률",
                   "YTD수익률", "1년수익률", "변동성", "최대낙폭",
                   "3년평균수익률", "5년평균수익률", "베타", "NAV", "괴리율",
                   "일평균거래대금", "환율", "중앙값간격_일"):
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if "최근_지급기준일" in frame.columns:
        frame["최근_지급기준일"] = pd.to_datetime(
            frame["최근_지급기준일"], errors="coerce").dt.date
    return frame


def fill_from_issuers() -> Path | None:
    """발행사 공식 자료(us_issuers.py)로 빈 칸을 먼저 채운다.

    야후와 달리 호출 제한이 없어서 **이쪽을 먼저 쓰고**, 남은 것만 야후로 채운다.
    실행: venv\\Scripts\\python src\\us_etf.py issuers
    """
    import us_issuers

    path = latest_csv()
    if path is None:
        print("먼저 python src/us_etf.py 로 수집하세요.")
        return None

    issuers = us_issuers.load_latest()
    if issuers.empty:
        print("발행사 자료가 없습니다. python src/us_issuers.py 를 먼저 실행하세요.")
        return None

    frame = load_latest()
    rate = float(frame["환율"].dropna().iloc[0]) if frame["환율"].notna().any() else 0.0
    got = issuers.drop_duplicates("티커").set_index("티커")

    filled = {"총보수": 0, "시가총액": 0, "운용사": 0, "카테고리": 0}
    for i, row in frame.iterrows():
        ticker = str(row["티커"]).strip()
        if ticker not in got.index:
            continue
        hit = got.loc[ticker]
        if pd.isna(row["총보수"]) and pd.notna(hit["총보수"]):
            frame.at[i, "총보수"] = hit["총보수"]
            filled["총보수"] += 1
        if pd.isna(row["시가총액"]) and pd.notna(hit["순자산"]):
            # 순자산은 달러라 다른 종목과 단위를 맞추려면 원화로 환산해야 한다
            frame.at[i, "시가총액"] = hit["순자산"] * rate if rate else hit["순자산"]
            filled["시가총액"] += 1
        if (pd.isna(row.get("운용사")) or not str(row.get("운용사")).strip()) \
                and str(hit["운용사"]).strip():
            frame.at[i, "운용사"] = hit["운용사"]
            filled["운용사"] += 1
        # 자산분류·기초지수명은 '카테고리' 칸에 모아 둔다 (분류 규칙이 이 글자를 본다)
        basis = " ".join(str(hit[c]).strip() for c in ("자산분류", "기초지수명")
                         if str(hit.get(c) or "").strip() not in ("", "nan"))
        if basis and (pd.isna(row.get("카테고리"))
                      or not str(row.get("카테고리")).strip()):
            frame.at[i, "카테고리"] = basis
            filled["카테고리"] += 1

    frame.to_csv(path, index=False, encoding="utf-8-sig")
    print("발행사 자료로 채움: "
          + " / ".join(f"{k} {v:,}개" for k, v in filled.items()))
    print(f"→ {path}")
    return path


def fill_details(limit: int = 400, workers: int = 4) -> Path | None:
    """총보수·시가총액이 빈 종목만 다시 채운다 (야후 호출 제한에 막혔을 때 쓴다).

    거래가 많은 종목부터 limit 개씩. 여러 번 나눠 돌리면 조금씩 채워진다.
    실행: venv\\Scripts\\python src\\us_etf.py fill
    """
    path = latest_csv()
    if path is None:
        print("먼저 python src/us_etf.py 로 수집하세요.")
        return None

    frame = load_latest()
    for column in FILLABLE + ("설정일", "괴리율"):
        if column not in frame.columns:
            frame[column] = None

    # 하나라도 비어 있으면 대상. 총보수만 보면, 총보수는 찼는데 설정일·베타가 빈
    # 종목(먼저 받아 둔 3,046개)이 영영 안 채워진다.
    missing = frame[list(FILLABLE)].isna().any(axis=1) | _blank(frame["설정일"])
    todo = frame[missing].nlargest(limit, "일평균거래대금")
    if todo.empty:
        print("채울 종목이 없습니다.")
        return path
    print(f"채울 대상 {len(todo):,}개 (남은 빈칸 {int(missing.sum()):,}개)")

    details = fetch_details(list(todo["티커"]), workers=workers)
    rate = float(frame["환율"].dropna().iloc[0]) if frame["환율"].notna().any() else 0.0
    got = details.set_index("티커")
    filled = 0
    for i, row in frame.iterrows():
        hit = got.loc[row["티커"]] if row["티커"] in got.index else None
        if hit is None:
            continue
        for column in FILLABLE:
            if pd.notna(row.get(column)) or pd.isna(hit.get(column)):
                continue
            value = hit[column]
            # 금액 칸은 수집 때와 같은 환율로 환산해야 다른 종목과 단위가 맞는다
            if column in MONEY_COLUMNS and rate:
                value = value * rate
            frame.at[i, column] = value
            if column == "총보수":
                filled += 1
        # 설정일은 글자라 isna() 로 안 걸린다 — 따로 본다
        if not str(row.get("설정일") or "").strip() and str(hit.get("설정일") or "").strip():
            frame.at[i, "설정일"] = hit["설정일"]
        # 값이 없으면 NaN 이 온다. float('nan') 은 truthy 라 str() 하면 'nan' 이 된다
        empty = pd.isna(row.get("운용사")) or not str(row.get("운용사")).strip()
        if empty and hit.get("운용사"):
            frame.at[i, "운용사"] = hit["운용사"]

    # NAV 를 새로 받았으면 괴리율을 다시 낸다 (둘 다 원화 환산된 상태라 비율은 같다)
    frame["괴리율"] = premium_discount(frame["종가"], frame["NAV"])

    frame.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"총보수 {filled:,}개 채움 → {path}")
    return path


def fill_loop(rounds: int = 8, wait_minutes: int = 20, limit: int = 800) -> None:
    """야후 호출 제한이 풀릴 때까지 **기다렸다가 다시** 채우기를 반복한다.

    제한은 몇 시간 단위가 아니라 몇십 분 단위로도 풀린다. 한 판에 0개면 그냥 기다린다.
    실행: venv\\Scripts\\python src\\us_etf.py loop
    """
    for turn in range(1, rounds + 1):
        frame = load_latest()
        left = int(frame["총보수"].isna().sum())
        if not left:
            print("빈칸이 없습니다. 끝.")
            return
        print(f"\n[{turn}/{rounds}] 남은 빈칸 {left:,}개")
        before = left
        fill_details(limit=limit)
        after = int(load_latest()["총보수"].isna().sum())
        print(f"  이번 판에 {before - after:,}개 채움")
        if after == 0:
            print("다 채웠습니다.")
            return
        if turn < rounds:
            print(f"  {wait_minutes}분 기다렸다 다시 시도합니다...", flush=True)
            time.sleep(wait_minutes * 60)


def main():
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "issuers":        # 발행사 공식 자료로 채우기 (호출 제한 없음)
        fill_from_issuers()
        return 0
    if command == "fill":
        # 몇 개까지 채울지 뒤에 숫자로 줄 수 있다: python src/us_etf.py fill 3600
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 400
        fill_details(limit=limit)
        return 0
    if command == "loop":           # 제한이 풀릴 때까지 기다렸다 반복
        fill_loop()
        return 0

    today = date.today()
    frame, dividends = collect()

    out = RAW_DIR / f"us_etf_{today.strftime('%Y%m%d')}.csv"
    frame.to_csv(out, index=False, encoding="utf-8-sig")
    div_out = RAW_DIR / f"us_배당_{today.strftime('%Y%m%d')}.csv"
    dividends.to_csv(div_out, index=False, encoding="utf-8-sig")
    print(f"\n저장: {out}")
    print(f"저장: {div_out} ({len(dividends):,}건)")
    print(f"종목 수: {len(frame):,}개 / 총보수 있음: {frame['총보수'].notna().sum():,}개")

    print("\n[확인용 상위 5행]")
    print(frame[["티커", "종목명", "지급횟수_12개월", "최근_분배율", "총보수"]]
          .head().to_string())


if __name__ == "__main__":
    from console import use_utf8_stdout   # 콘솔이 cp949여도 안 죽게
    use_utf8_stdout()
    from corp_ca import apply as apply_corp_ca   # 회사 HTTPS 가로채기 뒤에서도 받게
    apply_corp_ca()
    sys.exit(main())
