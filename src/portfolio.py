# -*- coding: utf-8 -*-
"""
7단계: 내 포트폴리오 계산

입력: data/portfolio/보유종목.csv  (계좌 사진을 클로드 코드에 올리면 만들어 준다)
  계좌,종목명,KRX코드,수량,매수단가,매수일,메모
  - KRX코드가 비면 종목명으로 찾는다
  - 매수일이 비면 '받은 분배금'은 최근 12개월 전체를 받은 것으로 가정(화면에 표시)

계산은 전부 이미 모아둔 데이터로 한다 (새 수집 없음):
  현재가·NAV  ← data/raw/krx_etf정보_*.csv (전종목 시세)
  분배금 이력  ← data/raw/seibro_분배금_*.csv (이익분배만)
  총보수·과세·분류 ← 같은 KRX 정보 CSV
"""
from datetime import date
from pathlib import Path
import sys

import pandas as pd

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "src"))

import etf_info                              # noqa: E402
from calendar_ics import parse_pay_months, TRI_CYCLE  # noqa: E402
from nav_erosion import isin_to_ticker       # noqa: E402
from whitelist import normalize_code         # noqa: E402

PORTFOLIO_DIR = BASE / "data" / "portfolio"
HOLDINGS_CSV = PORTFOLIO_DIR / "보유종목.csv"
RAW_DIR = BASE / "data" / "raw"

HOLDING_COLUMNS = ["계좌", "계좌유형", "종목명", "KRX코드", "수량", "매수단가", "매수일", "메모"]
TRADE_CSV = PORTFOLIO_DIR / "매매내역.csv"
TRADE_COLUMNS = ["계좌", "종목명", "KRX코드", "일자", "구분", "수량", "단가", "수수료"]
TARGET_CSV = PORTFOLIO_DIR / "목표비중.csv"

# 배당소득세 (소득세 14% + 지방소득세 1.4%). 일반 계좌 기본값
DIVIDEND_TAX_RATE = 0.154

# 계좌유형별 분배금 과세 — (즉시 원천징수율, 최종 예상세율, 설명)
# ⚠ 실제 세금은 나이·인출방식·금융소득종합과세 여부로 달라진다. 여기 값은 '보통의 경우'다.
ACCOUNT_TAX = {
    "일반(위탁)": (0.154, 0.154,
                 "분배금 받을 때 **바로 15.4% 원천징수**됩니다. "
                 "연 금융소득 2,000만원을 넘으면 종합과세로 더 낼 수 있습니다."),
    "연금저축": (0.0, 0.055,
               "**받을 때는 세금을 떼지 않습니다(과세이연).** 나중에 연금으로 찾을 때 "
               "연금소득세 3.3~5.5%(나이에 따라). 연금 외로 찾으면 기타소득세 16.5%."),
    "퇴직연금(IRP/DC)": (0.0, 0.055,
                        "연금저축과 같습니다. **받을 때 안 떼고**, 연금 수령 시 3.3~5.5%. "
                        "중도해지하면 기타소득세 16.5%."),
    "ISA": (0.0, 0.0,
            "계좌 안에서는 안 뗍니다. 만기 때 **순이익 200만원(서민형 400만원)까지 비과세**, "
            "넘는 금액만 9.9% 분리과세."),
}
DEFAULT_ACCOUNT_TYPE = "일반(위탁)"


def account_tax_rate(account_type: str, final: bool = False) -> float:
    """계좌유형별 세율. final=False면 지금 떼는 돈, True면 최종 예상."""
    rates = ACCOUNT_TAX.get(str(account_type or "").strip())
    if rates is None:
        rates = ACCOUNT_TAX[DEFAULT_ACCOUNT_TYPE]
    return rates[1] if final else rates[0]


def empty_holdings() -> pd.DataFrame:
    """빈 보유종목 표 (화면에서 직접 입력할 때 쓰는 뼈대)."""
    return pd.DataFrame({
        "계좌": pd.Series(dtype="string"),
        "계좌유형": pd.Series(dtype="string"),
        "종목명": pd.Series(dtype="string"),
        "KRX코드": pd.Series(dtype="string"),
        "수량": pd.Series(dtype="float"),
        "매수단가": pd.Series(dtype="float"),
        "매수일": pd.Series(dtype="object"),
        "메모": pd.Series(dtype="string"),
    })


def load_holdings(path: Path | None = None) -> pd.DataFrame:
    """보유종목 CSV를 읽는다. 파일이 없으면 빈 표."""
    path = path or HOLDINGS_CSV
    if not path.exists():
        return empty_holdings()
    frame = pd.read_csv(path, dtype={"KRX코드": str})
    for column in HOLDING_COLUMNS:
        if column not in frame.columns:
            frame[column] = None
    frame["수량"] = pd.to_numeric(frame["수량"], errors="coerce")
    frame["매수단가"] = pd.to_numeric(frame["매수단가"], errors="coerce")
    frame["매수일"] = pd.to_datetime(frame["매수일"], errors="coerce").dt.date
    for column in ("계좌", "계좌유형", "종목명", "KRX코드", "메모"):
        frame[column] = frame[column].fillna("").astype(str).str.strip()
    # 계좌유형이 비면 일반 계좌로 본다(세금을 과소평가하지 않는 쪽)
    frame["계좌유형"] = frame["계좌유형"].replace("", DEFAULT_ACCOUNT_TYPE)
    return frame[HOLDING_COLUMNS]


def mark_holdings(df: pd.DataFrame, holdings: pd.DataFrame | None = None) -> pd.DataFrame:
    """ETF 표에 '보유'(불리언) · '보유수량' 칸을 붙여서 돌려준다.

    화이트리스트와 같은 방식으로 **KRX코드 우선, 코드가 없을 때만 종목명**으로 맞춘다.
    보유종목.csv 가 없으면 전부 False 라 대시보드는 필터를 그냥 숨긴다.
    같은 종목을 여러 계좌에 나눠 가지고 있으면 수량은 합쳐서 센다.
    """
    holdings = load_holdings() if holdings is None else holdings
    out = df.copy()
    out["보유"] = False
    out["보유수량"] = 0.0
    if holdings.empty:
        return out

    qty = pd.to_numeric(holdings.get("수량"), errors="coerce").fillna(0.0)
    by_code, by_name = {}, {}
    for code, name, amount in zip(holdings["KRX코드"], holdings["종목명"], qty):
        code, name = normalize_code(code), str(name).strip()
        if code:
            by_code[code] = by_code.get(code, 0.0) + float(amount)
        elif name:
            by_name[name] = by_name.get(name, 0.0) + float(amount)

    df_code = df["KRX코드"].map(normalize_code) if "KRX코드" in df.columns \
        else pd.Series("", index=df.index)
    df_name = df["종목명"].astype(str).str.strip() if "종목명" in df.columns \
        else pd.Series("", index=df.index)

    amounts = [by_code.get(c) if c and c in by_code else by_name.get(n, 0.0)
               for c, n in zip(df_code, df_name)]
    out["보유수량"] = [float(a or 0.0) for a in amounts]
    out["보유"] = out["보유수량"] > 0
    return out


def save_holdings(frame: pd.DataFrame, path: Path | None = None) -> Path:
    """화면에서 고친 보유종목을 CSV로 되돌려 쓴다."""
    path = path or HOLDINGS_CSV
    path.parent.mkdir(parents=True, exist_ok=True)
    out = frame.reindex(columns=HOLDING_COLUMNS)
    out.to_csv(path, index=False, encoding="utf-8-sig")
    return path


def latest_distribution_csv() -> Path | None:
    files = sorted(RAW_DIR.glob("seibro_분배금_*.csv"))
    return files[-1] if files else None


def load_distributions() -> pd.DataFrame:
    """세이브로 원본에서 이익분배만. (ISIN, 지급기준일, 주당분배금)

    3단계-A 에서 확인한 대로 실제 주당분배금은 `결산과표기준가`(신형은 `주당분배금`)에 있다.
    수집 시점에 따라 컬럼명이 달라 둘 다 받아준다."""
    path = latest_distribution_csv()
    if path is None:
        return pd.DataFrame(columns=["ISIN", "지급기준일", "주당분배금"])
    raw = pd.read_csv(path, dtype={"ISIN": str})
    if "배당구분" in raw.columns:
        raw = raw[raw["배당구분"].astype(str).str.contains("이익", na=False)]
    amount = "결산과표기준가" if "결산과표기준가" in raw.columns else "주당분배금"
    frame = pd.DataFrame({
        "ISIN": raw["ISIN"].astype(str).str.strip(),
        "지급기준일": pd.to_datetime(raw["지급기준일"], format="%Y%m%d", errors="coerce"),
        "주당분배금": pd.to_numeric(raw[amount], errors="coerce"),
    })
    return frame.dropna(subset=["지급기준일", "주당분배금"])


def _resolve_codes(holdings: pd.DataFrame, info: pd.DataFrame,
                   names: pd.DataFrame) -> pd.DataFrame:
    """KRX코드가 비어 있으면 종목명으로 찾아 채운다."""
    out = holdings.copy()
    if names.empty:
        return out
    lookup = {str(n).strip(): str(c).strip()
              for n, c in zip(names["종목명"], names["KRX코드"]) if str(c).strip()}
    filled = []
    for code, name in zip(out["KRX코드"], out["종목명"]):
        code = str(code).strip()
        filled.append(code if code else lookup.get(str(name).strip(), ""))
    out["KRX코드"] = filled
    return out


def evaluate(holdings: pd.DataFrame, etf_table: pd.DataFrame,
             today: date | None = None) -> pd.DataFrame:
    """보유종목에 시세·분배금을 붙여 평가금액·손익을 계산한다.

    etf_table: output/분배ETF목록.xlsx (전체 시트). ISIN·종목명·총보수·분류가 들어 있다.
    """
    today = today or date.today()
    if holdings.empty:
        return pd.DataFrame()

    info = etf_info.load_latest()
    # ETF 목록에 KRX코드를 붙여 이름↔코드 대응표를 만든다.
    # 미국 종목은 ISIN 이 없고 '종목코드'에 티커(SCHD)가 들어 있다.
    names = etf_table.copy()
    if "KRX코드" not in names.columns:
        if "종목코드" in names.columns:
            names["KRX코드"] = names["종목코드"].fillna("").astype(str).str.strip()
        elif "ISIN" in names.columns:
            names["KRX코드"] = [_safe_ticker(v) for v in names["ISIN"]]
    holdings = _resolve_codes(holdings, info, names)

    merged = holdings.merge(
        names.drop(columns=[c for c in ("계좌", "메모") if c in names.columns]),
        on="KRX코드", how="left", suffixes=("", "_목록"))
    if "종목명_목록" in merged.columns:      # 코드로 찾은 정식 이름을 우선
        merged["종목명"] = merged["종목명_목록"].fillna(merged["종목명"])
        merged = merged.drop(columns="종목명_목록")

    if not info.empty:
        price = info[["KRX코드", "종가", "NAV", "기준일자"]].drop_duplicates("KRX코드")
        merged = merged.merge(price, on="KRX코드", how="left")
    for column in ("종가", "NAV"):
        if column not in merged.columns:
            merged[column] = pd.NA

    merged["현재가"] = pd.to_numeric(merged["종가"], errors="coerce")
    merged = _apply_us_prices(merged)     # 미국 종목은 KRX 시세에 없다
    merged["매수원금"] = merged["수량"] * merged["매수단가"]
    merged["평가금액"] = merged["수량"] * merged["현재가"]
    merged["평가손익"] = merged["평가금액"] - merged["매수원금"]
    merged["평가수익률"] = _pct(merged["평가손익"], merged["매수원금"])
    merged["괴리율"] = _pct(merged["현재가"] - pd.to_numeric(merged["NAV"], errors="coerce"),
                            pd.to_numeric(merged["NAV"], errors="coerce"))

    dist = load_distributions()
    us_dist = _us_distributions()          # 미국 종목은 야후 배당 이력을 쓴다
    received, per_share_12m, since_labels = [], [], []
    for _, row in merged.iterrows():
        isin = str(row.get("ISIN", "") or "")
        if _is_us(row):
            code = str(row.get("KRX코드", "") or "").strip()
            mine = us_dist[us_dist["ISIN"] == code] if code else us_dist.iloc[0:0]
        else:
            mine = dist[dist["ISIN"] == isin] if isin else dist.iloc[0:0]
        year_ago = pd.Timestamp(today) - pd.DateOffset(years=1)
        per_share_12m.append(mine[mine["지급기준일"] >= year_ago]["주당분배금"].sum())

        bought = row.get("매수일")
        if pd.notna(bought) and bought:
            since = mine[mine["지급기준일"] >= pd.Timestamp(bought)]
            since_labels.append("매수일 이후")
        else:
            since = mine[mine["지급기준일"] >= year_ago]
            since_labels.append("최근 12개월(매수일 미입력)")
        received.append(since["주당분배금"].sum() * (row["수량"] or 0))

    merged["받은분배금"] = received
    merged["기준"] = since_labels
    merged["연간주당분배금"] = per_share_12m

    merged["총손익"] = merged["평가손익"] + merged["받은분배금"]
    merged["총수익률"] = _pct(merged["총손익"], merged["매수원금"])
    merged["분배금회수율"] = _pct(merged["받은분배금"], merged["매수원금"])
    # 내 매수단가 기준 연 분배수익률 (Yield on Cost) — 배당투자의 핵심 지표
    merged["매수가대비분배율"] = _pct(merged["연간주당분배금"], merged["매수단가"])
    merged["현재가대비분배율"] = _pct(merged["연간주당분배금"], merged["현재가"])
    merged["예상다음분배금"] = (pd.to_numeric(merged.get("최근_주당분배금"), errors="coerce")
                               * merged["수량"])
    # 계좌유형별 과세 — 연금·ISA 는 받을 때 안 뗀다
    if "계좌유형" not in merged.columns:
        merged["계좌유형"] = DEFAULT_ACCOUNT_TYPE
    merged["계좌유형"] = merged["계좌유형"].fillna("").replace("", DEFAULT_ACCOUNT_TYPE)
    # 미국 상장분은 계좌유형과 무관하게 **현지에서 15%** 를 떼고 들어온다.
    # (애초에 연금·ISA 계좌로는 못 사서 일반 계좌에만 있을 종목이다)
    is_us = merged.apply(_is_us, axis=1)
    withheld = merged["계좌유형"].map(lambda t: account_tax_rate(t, final=False))
    eventual = merged["계좌유형"].map(lambda t: account_tax_rate(t, final=True))
    withheld = withheld.mask(is_us, US_WITHHOLDING_RATE)
    eventual = eventual.mask(is_us, US_WITHHOLDING_RATE)
    merged["원천징수세율"] = withheld * 100
    merged["최종예상세율"] = eventual * 100
    merged["세후예상분배금"] = merged["예상다음분배금"] * (1 - withheld)
    merged["받은분배금_세후"] = merged["받은분배금"] * (1 - withheld)
    # 일반 계좌였다면 뗐을 세금과의 차이 = 절세계좌 덕분에 아낀 돈
    merged["절세효과"] = merged["받은분배금"] * (DIVIDEND_TAX_RATE - withheld)
    merged["연간예상분배금"] = merged["연간주당분배금"] * merged["수량"]
    merged["연간보수"] = merged["평가금액"] * pd.to_numeric(
        merged.get("총보수"), errors="coerce") / 100

    total = merged["평가금액"].sum(skipna=True)
    merged["비중"] = _pct(merged["평가금액"], total) if total else pd.NA
    return merged


def _safe_ticker(isin) -> str:
    try:
        return isin_to_ticker(isin)
    except (ValueError, TypeError):
        return ""


# ── 미국 상장 종목 ─────────────────────────────────────────
US_MARKET = "미국"
# 미국 배당은 현지에서 15% 원천징수되고 국내에서 더 떼지 않는다(조세조약).
# 다만 금융소득종합과세 대상에는 들어간다.
US_WITHHOLDING_RATE = 0.15
# 연금·ISA 계좌로는 미국 직접 상장 ETF 를 살 수 없다
US_BLOCKED_ACCOUNTS = ("연금저축", "퇴직연금(IRP/DC)", "ISA")


def _is_us(row) -> bool:
    """미국 직접 상장 종목인가. 목록의 '상장시장' 값으로만 판단한다."""
    return str(row.get("상장시장", "") or "").strip() == US_MARKET


def _apply_us_prices(merged: pd.DataFrame) -> pd.DataFrame:
    """미국 종목의 현재가를 us_etf 수집분(원화 환산 종가)으로 채운다."""
    if "상장시장" not in merged.columns or not merged.apply(_is_us, axis=1).any():
        return merged
    import us_etf

    us = us_etf.load_latest()
    if us.empty:
        return merged
    price = dict(zip(us["티커"].astype(str).str.strip(),
                     pd.to_numeric(us["종가"], errors="coerce")))
    asof = str(us["기준일자"].iloc[0]) if "기준일자" in us.columns and len(us) else ""
    # 국내 종목이 하나도 없으면 기준일자 칸이 숫자형으로 잡혀 있다 → 글자를 넣으려면 바꿔야 한다
    if asof and "기준일자" in merged.columns:
        merged["기준일자"] = merged["기준일자"].astype(object)
    for i, row in merged.iterrows():
        if not _is_us(row) or pd.notna(merged.at[i, "현재가"]):
            continue
        hit = price.get(str(row.get("KRX코드", "") or "").strip())
        if hit is not None and pd.notna(hit):
            merged.at[i, "현재가"] = hit
            if asof and "기준일자" in merged.columns:
                merged.at[i, "기준일자"] = asof
    return merged


def _us_distributions() -> pd.DataFrame:
    """미국 배당 이력을 국내와 같은 칸 이름으로 바꿔 준다.

    ISIN 자리에는 티커를, 지급기준일 자리에는 배당락일을 넣는다
    (미국은 배당락일이 곧 받을 사람을 가르는 날이라 역할이 같다).
    """
    import us_etf

    frame = us_etf.load_dividends()
    if frame.empty:
        return pd.DataFrame(columns=["ISIN", "지급기준일", "주당분배금"])
    return pd.DataFrame({
        "ISIN": frame["티커"].astype(str).str.strip(),
        "지급기준일": frame["배당락일"],
        "주당분배금": frame["주당분배금"],
    })


def account_conflicts(evaluated: pd.DataFrame) -> pd.DataFrame:
    """계좌유형으로는 못 사는 종목이 들어와 있는지 (연금·ISA 에 미국 종목)."""
    if evaluated.empty or "상장시장" not in evaluated.columns:
        return evaluated.iloc[0:0]
    is_us = evaluated.apply(_is_us, axis=1)
    blocked = evaluated["계좌유형"].astype(str).str.strip().isin(US_BLOCKED_ACCOUNTS)
    return evaluated[is_us & blocked]


def _pct(numerator, denominator):
    """0으로 나누기를 피한 백분율. 분모는 Series든 스칼라든 받는다."""
    num = pd.to_numeric(numerator, errors="coerce")
    den = pd.to_numeric(denominator, errors="coerce")
    if isinstance(den, pd.Series):
        den = den.where(den != 0)
    elif not den:
        return num * pd.NA
    return num / den * 100


def summarize(evaluated: pd.DataFrame) -> dict:
    """계좌 전체 합계."""
    if evaluated.empty:
        return {}
    principal = evaluated["매수원금"].sum(skipna=True)
    value = evaluated["평가금액"].sum(skipna=True)
    gain = evaluated["평가손익"].sum(skipna=True)
    dividend = evaluated["받은분배금"].sum(skipna=True)
    return {
        "매수원금": principal,
        "평가금액": value,
        "평가손익": gain,
        "평가수익률": (gain / principal * 100) if principal else 0.0,
        "받은분배금": dividend,
        "총손익": gain + dividend,
        "총수익률": ((gain + dividend) / principal * 100) if principal else 0.0,
        "분배금회수율": (dividend / principal * 100) if principal else 0.0,
        "연간보수": evaluated["연간보수"].sum(skipna=True),
        "종목수": len(evaluated),
        "계좌수": evaluated["계좌"].replace("", pd.NA).nunique(dropna=True),
        "받은분배금_세후": evaluated.get("받은분배금_세후", pd.Series(dtype=float)).sum(skipna=True),
        "절세효과": evaluated.get("절세효과", pd.Series(dtype=float)).sum(skipna=True),
        "연간예상분배금": evaluated.get("연간예상분배금", pd.Series(dtype=float)).sum(skipna=True),
    }


# ── 현재 시세 새로고침 ────────────────────────────────────
def fetch_live_prices(today: date | None = None) -> tuple[pd.DataFrame, str]:
    """KRX 전종목 시세를 지금 다시 받아온다. (표, 기준일자)

    ⚠ KRX가 주는 값이라 **실시간 체결가가 아니라 약 20분 지연 시세**다.
    장중이면 그 시각까지의 시세, 휴장이면 직전 개장일 종가가 온다.
    """
    today = today or date.today()
    frame, trade_date = etf_info.latest_marketcap(today)
    return frame[["ISIN", "종가", "NAV"]], trade_date


def apply_live_prices(evaluated: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    """새로 받은 시세로 현재가·평가금액·손익을 다시 계산한다."""
    if evaluated.empty or prices.empty:
        return evaluated
    out = evaluated.drop(columns=[c for c in ("종가", "NAV") if c in evaluated.columns])
    out = out.merge(prices, on="ISIN", how="left")
    out["현재가"] = pd.to_numeric(out["종가"], errors="coerce").fillna(out["현재가"])
    out["평가금액"] = out["수량"] * out["현재가"]
    out["평가손익"] = out["평가금액"] - out["매수원금"]
    out["평가수익률"] = _pct(out["평가손익"], out["매수원금"])
    out["괴리율"] = _pct(out["현재가"] - pd.to_numeric(out["NAV"], errors="coerce"),
                         pd.to_numeric(out["NAV"], errors="coerce"))
    out["총손익"] = out["평가손익"] + out["받은분배금"]
    out["총수익률"] = _pct(out["총손익"], out["매수원금"])
    out["현재가대비분배율"] = _pct(out["연간주당분배금"], out["현재가"])
    out["연간보수"] = out["평가금액"] * pd.to_numeric(out.get("총보수"), errors="coerce") / 100
    total = out["평가금액"].sum(skipna=True)
    out["비중"] = _pct(out["평가금액"], total) if total else pd.NA
    return out


# ── 월별 분배금 현금흐름 ───────────────────────────────────
CYCLE_MONTHS = {"위클리": 0.25, "월": 1, "분기": 3, "반기": 6, "연": 12}


def monthly_cashflow(evaluated: pd.DataFrame, months: int = 12,
                     today: date | None = None) -> pd.DataFrame:
    """앞으로 N개월 예상 분배금을 달별로 쌓는다 (세전·세후).

    직전 지급기준일에서 분배주기만큼 더해가며 예상 지급월을 잡는다.
    공시 전 추정이라 실제와 다를 수 있다.
    """
    today = today or date.today()
    if evaluated.empty:
        return pd.DataFrame(columns=["월", "세전", "세후"])

    start = pd.Timestamp(today).normalize()
    limit = start + pd.DateOffset(months=months)
    rows = []
    for _, row in evaluated.iterrows():
        cycle_name = str(row.get("분배주기", ""))
        tri_months = (parse_pay_months(row.get("지급월목록"))
                      if cycle_name == TRI_CYCLE else None)
        cycle = 4 if tri_months else CYCLE_MONTHS.get(cycle_name, None)
        per_share = pd.to_numeric(row.get("최근_주당분배금"), errors="coerce")
        quantity = row.get("수량")
        if not cycle or pd.isna(per_share) or not quantity:
            continue
        amount = per_share * quantity
        after = amount * (1 - account_tax_rate(row.get("계좌유형"), final=False))
        step = max(int(round(cycle)), 1)          # 위클리도 월 단위로 묶어서 본다
        per_month = amount * (1 / cycle if cycle < 1 else 1)
        after_month = after * (1 / cycle if cycle < 1 else 1)

        base = pd.to_datetime(row.get("최근_지급기준일"), errors="coerce")
        if tri_months:
            # 연3회는 간격이 3·3·6개월이라 고정 걸음으로는 달이 밀린다
            # → 지급월목록의 달에만 놓는다. 직전 지급이 있던 달까지는 건너뛴다
            cursor = pd.Timestamp(start.year, start.month, 1)
            while cursor <= limit:
                paid_already = pd.notna(base) and \
                    cursor <= pd.Timestamp(base.year, base.month, 1)
                if cursor.month in tri_months and not paid_already:
                    rows.append({"월": cursor.strftime("%Y-%m"),
                                 "세전": per_month, "세후": after_month})
                cursor += pd.DateOffset(months=1)
            continue
        cursor = (base if pd.notna(base) else start) + pd.DateOffset(months=step)
        while cursor < start:
            cursor += pd.DateOffset(months=step)
        while cursor <= limit:
            rows.append({"월": cursor.strftime("%Y-%m"),
                         "세전": per_month, "세후": after_month})
            cursor += pd.DateOffset(months=step)

    if not rows:
        return pd.DataFrame(columns=["월", "세전", "세후"])
    flow = pd.DataFrame(rows).groupby("월", as_index=False)[["세전", "세후"]].sum()
    return flow.sort_values("월").reset_index(drop=True)


def received_by_month(evaluated: pd.DataFrame, months: int = 12,
                      today: date | None = None) -> pd.DataFrame:
    """실제로 받은 분배금을 달별로 (예상과 비교용)."""
    today = today or date.today()
    if evaluated.empty:
        return pd.DataFrame(columns=["월", "받은금액"])
    dist = load_distributions()
    if dist.empty:
        return pd.DataFrame(columns=["월", "받은금액"])
    since = pd.Timestamp(today) - pd.DateOffset(months=months)
    rows = []
    for _, row in evaluated.iterrows():
        isin, quantity = str(row.get("ISIN", "") or ""), row.get("수량")
        if not isin or not quantity:
            continue
        mine = dist[(dist["ISIN"] == isin) & (dist["지급기준일"] >= since)]
        bought = row.get("매수일")
        if pd.notna(bought) and bought:
            mine = mine[mine["지급기준일"] >= pd.Timestamp(bought)]
        for _, d in mine.iterrows():
            rows.append({"월": d["지급기준일"].strftime("%Y-%m"),
                         "받은금액": d["주당분배금"] * quantity})
    if not rows:
        return pd.DataFrame(columns=["월", "받은금액"])
    return (pd.DataFrame(rows).groupby("월", as_index=False)["받은금액"].sum()
            .sort_values("월").reset_index(drop=True))


# ── 목표 비중 / 리밸런싱 ───────────────────────────────────
def load_targets(path: Path | None = None) -> pd.DataFrame:
    """목표비중 CSV (구분, 목표비중). 없으면 빈 표."""
    path = path or TARGET_CSV
    if not path.exists():
        return pd.DataFrame(columns=["구분", "목표비중"])
    frame = pd.read_csv(path)
    if "구분" not in frame.columns or "목표비중" not in frame.columns:
        return pd.DataFrame(columns=["구분", "목표비중"])
    frame["목표비중"] = pd.to_numeric(frame["목표비중"], errors="coerce")
    frame["구분"] = frame["구분"].astype(str).str.strip()
    return frame.dropna(subset=["목표비중"])


def save_targets(frame: pd.DataFrame, path: Path | None = None) -> Path:
    path = path or TARGET_CSV
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.reindex(columns=["구분", "목표비중"]).to_csv(
        path, index=False, encoding="utf-8-sig")
    return path


def rebalance(evaluated: pd.DataFrame, targets: pd.DataFrame,
              column: str = "종목명", band: float = 5.0) -> pd.DataFrame:
    """목표 비중 대비 얼마나 벗어났는지, 얼마를 사고팔아야 하는지.

    band: 이 %p 를 넘게 벌어지면 '조정' 으로 표시 (기본 5%p)
    """
    if evaluated.empty or targets.empty:
        return pd.DataFrame(columns=["구분", "목표비중", "현재비중", "차이", "조정금액", "판정"])
    current = breakdown(evaluated, column).rename(columns={column: "구분"})
    total = evaluated["평가금액"].sum(skipna=True)

    merged = targets.merge(current[["구분", "비중", "평가금액"]], on="구분", how="outer")
    merged["목표비중"] = merged["목표비중"].fillna(0.0)
    merged["현재비중"] = merged["비중"].fillna(0.0)
    merged["평가금액"] = merged["평가금액"].fillna(0.0)
    merged["차이"] = merged["현재비중"] - merged["목표비중"]
    merged["조정금액"] = total * (merged["목표비중"] - merged["현재비중"]) / 100
    merged["판정"] = [
        "유지" if abs(gap) <= band else ("더 사기" if gap < 0 else "덜기")
        for gap in merged["차이"]
    ]
    return (merged[["구분", "목표비중", "현재비중", "차이", "조정금액", "판정"]]
            .sort_values("차이").reset_index(drop=True))


# ── 매매내역 → 실현손익 ────────────────────────────────────
def load_trades(path: Path | None = None) -> pd.DataFrame:
    """매매내역 CSV. 계좌,종목명,KRX코드,일자,구분(매수/매도),수량,단가,수수료"""
    path = path or TRADE_CSV
    if not path.exists():
        return pd.DataFrame(columns=TRADE_COLUMNS)
    frame = pd.read_csv(path, dtype={"KRX코드": str})
    for column in TRADE_COLUMNS:
        if column not in frame.columns:
            frame[column] = None
    frame["일자"] = pd.to_datetime(frame["일자"], errors="coerce")
    for column in ("수량", "단가", "수수료"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0.0)
    for column in ("계좌", "종목명", "KRX코드", "구분"):
        frame[column] = frame[column].fillna("").astype(str).str.strip()
    return frame[TRADE_COLUMNS].dropna(subset=["일자"]).sort_values("일자")


def save_trades(frame: pd.DataFrame, path: Path | None = None) -> Path:
    path = path or TRADE_CSV
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.reindex(columns=TRADE_COLUMNS).to_csv(path, index=False, encoding="utf-8-sig")
    return path


def realized_pnl(trades: pd.DataFrame) -> pd.DataFrame:
    """매도 건별 실현손익. **이동평균법**으로 매도 시점의 평균단가를 쓴다.

    (국내 증권사 대부분이 쓰는 방식. 선입선출과는 결과가 다를 수 있다)
    """
    if trades.empty:
        return pd.DataFrame(columns=["일자", "계좌", "종목명", "수량", "매도단가",
                                     "평균매수단가", "실현손익", "수익률"])
    rows = []
    # 계좌+종목별로 따로 평균단가를 굴린다
    for (account, name), group in trades.groupby(["계좌", "종목명"], sort=False):
        held, avg = 0.0, 0.0
        for _, t in group.sort_values("일자").iterrows():
            quantity, price, fee = t["수량"], t["단가"], t["수수료"]
            if t["구분"] == "매수":
                cost = held * avg + quantity * price + fee
                held += quantity
                avg = cost / held if held else 0.0
            elif t["구분"] == "매도" and held > 0:
                sold = min(quantity, held)
                gain = (price - avg) * sold - fee
                rows.append({
                    "일자": t["일자"].date(), "계좌": account, "종목명": name,
                    "수량": sold, "매도단가": price, "평균매수단가": avg,
                    "실현손익": gain,
                    "수익률": (gain / (avg * sold) * 100) if avg and sold else 0.0,
                })
                held -= sold
    return pd.DataFrame(rows)


def breakdown(evaluated: pd.DataFrame, column: str) -> pd.DataFrame:
    """지역·섹터·계좌 등으로 묶은 비중표 (집중도 확인용)."""
    if evaluated.empty or column not in evaluated.columns:
        return pd.DataFrame(columns=[column, "평가금액", "비중"])
    grouped = (evaluated.assign(**{column: evaluated[column].replace("", "미분류").fillna("미분류")})
               .groupby(column, dropna=False)["평가금액"].sum().reset_index())
    total = grouped["평가금액"].sum()
    grouped["비중"] = grouped["평가금액"] / total * 100 if total else 0.0
    return grouped.sort_values("평가금액", ascending=False).reset_index(drop=True)
