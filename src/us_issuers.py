# -*- coding: utf-8 -*-
"""
15단계: 미국 ETF 발행사 공식 자료 수집

야후(quoteSummary)는 호출 제한이 세서 총보수·순자산을 다 못 받는다.
발행사가 직접 공개하는 파일에는 **제한이 없고 값도 정확하다**.

| 소스 | 형식 | 받는 것 | 범위 |
|---|---|---|---|
| iShares (BlackRock) | JSON (제품 스크리너) | 총보수·순자산·자산분류·지역·12개월 배당수익률·ISIN | 한 번에 532개 |
| SPDR (State Street) | XLSX (제품 데이터) | 총보수·순자산·자산분류·**기초지수명**·분배주기·ISIN | 한 번에 177개 |
| stockanalysis.com | JSON (SvelteKit) | 총보수·순자산·배당수익률·자산분류 | **발행사 무관**, 종목당 1회 |

앞의 둘은 대형 발행사를 한 번에 쓸어 오고, **야후에 값이 아예 없는 소규모 발행사**
(Direxion·Roundhill·NEOS·Nuveen…)는 stockanalysis 가 메운다.
야후 `quoteSummary` 는 호출 제한이 세고 이런 종목은 애초에 값이 없다.

결과: data/raw/us_발행사_YYYYMMDD.csv
실행:
  venv\\Scripts\\python src\\us_issuers.py            # iShares + SPDR 전체
  venv\\Scripts\\python src\\us_issuers.py missing    # 아직 총보수가 빈 종목만 보충
"""
from datetime import date
from pathlib import Path
import io
import sys

import pandas as pd

BASE = Path(__file__).resolve().parent.parent
RAW_DIR = BASE / "data" / "raw"
RAW_DIR.mkdir(parents=True, exist_ok=True)

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

ISHARES_URL = (
    "https://www.ishares.com/us/product-screener/product-screener-v3.1.jsn"
    "?dcrPath=/templatedata/config/product-screener-v3/data/en/us-ishares/"
    "ishares-product-screener-backend-config&siteEntryPassthrough=true"
)
SPDR_URL = (
    "https://www.ssga.com/us/en/intermediary/library-content/products/fund-data/etfs/us/"
    "spdr-product-data-us-en.xlsx"
)

OUTPUT_COLUMNS = ["티커", "종목명", "운용사", "총보수", "순자산", "자산분류",
                  "지역", "기초지수명", "연간분배율", "ISIN", "출처"]


def _num(value):
    """iShares 는 {'d': '0.03', 'r': 0.03} 꼴로 준다 — 계산용 값 r 을 꺼낸다."""
    if isinstance(value, dict):
        value = value.get("r")
    return pd.to_numeric(value, errors="coerce")


def fetch_ishares() -> pd.DataFrame:
    """iShares 제품 스크리너 JSON."""
    import requests

    data = requests.get(ISHARES_URL, headers=UA, timeout=90).json()
    rows = []
    for item in data.values():
        ticker = str(item.get("localExchangeTicker") or "").strip()
        if not ticker:
            continue
        region = " ".join(str(item.get(k) or "").strip()
                          for k in ("aladdinCountry", "aladdinRegion")).strip()
        rows.append({
            "티커": ticker,
            "종목명": str(item.get("fundName") or "").strip(),
            "운용사": "iShares",
            # fees 가 비면 운용보수(mgt)를 쓴다
            "총보수": _num(item.get("fees")) if item.get("fees") else _num(item.get("mgt")),
            "순자산": _num(item.get("totalNetAssets")),
            "자산분류": str(item.get("aladdinAssetClass") or "").strip(),
            "지역": region,
            "기초지수명": "",       # iShares 스크리너에는 지수명이 없다
            "연간분배율": _num(item.get("twelveMonTrlYield")),
            "ISIN": str(item.get("isin") or "").strip(),
            "출처": "iShares",
        })
    return pd.DataFrame(rows, columns=OUTPUT_COLUMNS)


def _spdr_percent(series: pd.Series) -> pd.Series:
    """'0.10%' → 0.10 (결측은 '-' 로 온다)."""
    text = series.astype(str).str.strip().str.rstrip("%")
    return pd.to_numeric(text.replace({"-": None, "nan": None, "": None}),
                         errors="coerce")


def _spdr_money(series: pd.Series) -> pd.Series:
    """'$27,846.31 M' → 27846310000.0 (M=백만, B=십억)."""
    text = (series.astype(str).str.strip()
            .str.replace("$", "", regex=False).str.replace(",", "", regex=False))
    unit = text.str.extract(r"([MBK])\s*$", expand=False).fillna("")
    number = pd.to_numeric(text.str.replace(r"[MBK]\s*$", "", regex=True).str.strip()
                           .replace({"-": None, "nan": None, "": None}), errors="coerce")
    scale = unit.map({"K": 1e3, "M": 1e6, "B": 1e9}).fillna(1.0)
    return number * scale


def fetch_spdr() -> pd.DataFrame:
    """SPDR 제품 데이터 XLSX (첫 줄은 안내문이라 건너뛴다)."""
    import requests

    content = requests.get(SPDR_URL, headers=UA, timeout=90,
                           allow_redirects=True).content
    raw = pd.read_excel(io.BytesIO(content), skiprows=1)
    raw.columns = [str(c).strip() for c in raw.columns]

    def column(*names):
        """이름이 조금씩 바뀌어도 찾아지게 — 부분 일치로 고른다."""
        for name in names:
            for c in raw.columns:
                if name.lower() in c.lower():
                    return raw[c]
        return pd.Series("", index=raw.index)

    # 값이 '0.10%' · '$27,846.31 M' 같은 글자로 온다 → 숫자로 바꿔야 한다
    fee = _spdr_percent(column("Net Expense Ratio"))
    fee = fee.fillna(_spdr_percent(column("Gross Expense Ratio")))
    frame = pd.DataFrame({
        "티커": column("Ticker").astype(str).str.strip(),
        "종목명": column("Name").astype(str).str.strip(),
        "운용사": "SPDR",
        "총보수": fee,
        "순자산": _spdr_money(column("Total Net Assets")),
        "자산분류": column("Asset Class").astype(str).str.strip(),
        "지역": "",
        "기초지수명": column("Primary Index").astype(str).str.strip(),
        "연간분배율": _spdr_percent(column("Fund Dividend Yield")),
        "ISIN": column("ISIN").astype(str).str.strip(),
        "출처": "SPDR",
    })
    frame = frame[frame["티커"].str.match(r"^[A-Z]{1,5}$", na=False)]
    return frame[OUTPUT_COLUMNS].reset_index(drop=True)


# ── stockanalysis.com — 발행사를 가리지 않는 보완 소스 ────────────
# 야후에 값이 **아예 없는** 소규모 발행사(Direxion·Roundhill·NEOS·Nuveen…)를 여기서 채운다.
# 종목당 요청 1회라 iShares·SPDR 처럼 한 번에 받지는 못한다 → 빈 종목만 골라 부른다.
SA_URL = "https://stockanalysis.com/etf/{ticker}/__data.json"
# SvelteKit 이 내려주는 data 배열은 '값이 있는 자리 번호'로 서로를 가리킨다
SA_FIELDS = ("name", "expenseRatio", "aum", "dividendYield", "assetClass", "issuer")


def _sa_resolve(data: list, value):
    """가리키는 자리 번호를 실제 값으로 바꾼다."""
    if isinstance(value, int) and 0 <= value < len(data):
        return data[value]
    return value


def _sa_money(text) -> float | None:
    """'$1.11B' · '$146.17M' → 숫자(달러)."""
    if not isinstance(text, str):
        return None
    clean = text.replace("$", "").replace(",", "").strip()
    scale = {"T": 1e12, "B": 1e9, "M": 1e6, "K": 1e3}.get(clean[-1:].upper(), 1.0)
    if scale != 1.0:
        clean = clean[:-1]
    try:
        return float(clean) * scale
    except ValueError:
        return None


def _sa_percent(text) -> float | None:
    """'0.99%' → 0.99"""
    if not isinstance(text, str):
        return None
    try:
        return float(text.strip().rstrip("%"))
    except ValueError:
        return None


def fetch_stockanalysis(tickers: list[str], workers: int = 6) -> pd.DataFrame:
    """티커 목록만큼 stockanalysis.com 을 훑는다 (한 종목씩이라 스레드로 나눈다)."""
    from concurrent.futures import ThreadPoolExecutor
    import requests

    session = requests.Session()
    session.headers.update(UA)

    def one(ticker: str) -> dict | None:
        try:
            payload = session.get(SA_URL.format(ticker=ticker.lower()), timeout=30).json()
        except Exception:
            return None
        found = {}
        for node in payload.get("nodes", []):
            data = node.get("data")
            if not isinstance(data, list) or not data:
                continue
            for entry in data:
                if isinstance(entry, dict):
                    for key in SA_FIELDS:
                        if key in entry and key not in found:
                            found[key] = _sa_resolve(data, entry[key])
        if not found.get("expenseRatio") and not found.get("aum"):
            return None
        return {
            "티커": ticker,
            "종목명": str(found.get("name") or "").strip(),
            "운용사": str(found.get("issuer") or "").strip(),
            "총보수": _sa_percent(found.get("expenseRatio")),
            "순자산": _sa_money(found.get("aum")),
            "자산분류": str(found.get("assetClass") or "").strip(),
            "지역": "",
            "기초지수명": "",
            "연간분배율": _sa_percent(found.get("dividendYield")),
            "ISIN": "",
            "출처": "stockanalysis",
        }

    rows = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for i, row in enumerate(pool.map(one, tickers), start=1):
            if row:
                rows.append(row)
            if i % 50 == 0 or i == len(tickers):
                print(f"  stockanalysis {i:,}/{len(tickers):,}개 (받음 {len(rows):,})",
                      flush=True)
    return pd.DataFrame(rows, columns=OUTPUT_COLUMNS)


FETCHERS = [("iShares", fetch_ishares), ("SPDR", fetch_spdr)]


def collect() -> pd.DataFrame:
    """발행사별로 받아 하나로 합친다. 한 곳이 실패해도 나머지는 살린다."""
    frames = []
    for name, fetcher in FETCHERS:
        try:
            frame = fetcher()
            print(f"  {name}: {len(frame):,}개")
            frames.append(frame)
        except Exception as exc:
            print(f"  {name}: 실패 ({type(exc).__name__}: {str(exc)[:60]})")
    if not frames:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    out = pd.concat(frames, ignore_index=True)
    return out.drop_duplicates("티커", keep="first").reset_index(drop=True)


def latest_csv() -> Path | None:
    files = sorted(RAW_DIR.glob("us_발행사_*.csv"))
    return files[-1] if files else None


def load_latest() -> pd.DataFrame:
    """저장된 발행사 자료. 없으면 빈 표."""
    path = latest_csv()
    if path is None:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    frame = pd.read_csv(path, dtype={"티커": str})
    for column in ("총보수", "순자산", "연간분배율"):
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


def fill_missing(limit: int = 400) -> pd.DataFrame:
    """us_etf 수집분에서 **총보수가 빈 종목만** stockanalysis 로 받아 덧붙인다.

    실행: venv\\Scripts\\python src\\us_issuers.py missing
    """
    import us_etf

    frame = us_etf.load_latest()
    if frame.empty:
        print("먼저 python src/us_etf.py 로 수집하세요.")
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    have = set(load_latest()["티커"].astype(str)) if latest_csv() else set()
    todo = frame[frame["총보수"].isna()]
    todo = todo[~todo["티커"].astype(str).isin(have)]
    todo = todo.nlargest(limit, "일평균거래대금")
    if todo.empty:
        print("stockanalysis 로 받을 종목이 없습니다.")
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    print(f"총보수가 빈 종목 {len(todo):,}개를 stockanalysis 에서 받습니다")
    return fetch_stockanalysis(list(todo["티커"].astype(str)))


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "missing":
        extra = fill_missing()
        if extra.empty:
            return 0
        merged = pd.concat([load_latest(), extra], ignore_index=True)
        merged = merged.drop_duplicates("티커", keep="first")
        out = RAW_DIR / f"us_발행사_{date.today().strftime('%Y%m%d')}.csv"
        merged.to_csv(out, index=False, encoding="utf-8-sig")
        print(f"\n{len(extra):,}개 추가 → 합계 {len(merged):,}개 / {out}")
        return 0

    print("발행사 공식 자료 수집")
    frame = collect()
    if frame.empty:
        print("받은 게 없습니다.")
        return 1

    out = RAW_DIR / f"us_발행사_{date.today().strftime('%Y%m%d')}.csv"
    frame.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n저장: {out}")
    print(f"종목 {len(frame):,}개 / 총보수 {frame['총보수'].notna().sum():,}개 "
          f"/ 순자산 {frame['순자산'].notna().sum():,}개 "
          f"/ 기초지수명 {(frame['기초지수명'].astype(str).str.strip() != '').sum():,}개")
    print("\n[확인용 상위 5행]")
    print(frame[["티커", "종목명", "총보수", "순자산", "출처"]].head().to_string())
    return 0


if __name__ == "__main__":
    from console import use_utf8_stdout   # 콘솔이 cp949여도 안 죽게
    use_utf8_stdout()
    from corp_ca import apply as apply_corp_ca   # 회사 HTTPS 가로채기 뒤에서도 받게
    apply_corp_ca()
    sys.exit(main())
