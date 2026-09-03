# -*- coding: utf-8 -*-
"""관심종목 — 아직 안 산 ETF 를 넣어 보고 "사면 얼마가 들어오나"를 미리 계산한다.

파일: data/reference/관심종목.csv — **수급 탭과 같은 파일**을 쓴다 (관심종목은 하나면 된다)
컬럼: 종목코드,종목명,수량,투자금액,메모 (수급 탭은 앞 두 칸만 쓰고 나머지는 보존한다)
  - 종목코드: 국내 6자리 또는 미국 티커(SCHD). 비우면 종목명으로 찾는다
  - 수량 또는 투자금액 **한쪽만** 넣으면 된다. 투자금액을 넣으면
    현재가로 나눠 정수 주수로 바꾸고, 실제 투자금액 = 주수 × 현재가 로 다시 잡는다

계산 가정 (화면에도 적는다):
  - **직전 12개월 분배가 그대로 반복**된다고 본다 — 1회분 = 연간주당분배금 ÷ 지급횟수
  - 예상 지급일은 최근 지급기준일에 분배주기를 더해가며 잡는다 (공시 전 추정)
  - 가격 변동은 예측하지 않는다. 1년수익률은 **과거 참고값**으로만 보여 준다
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
import sys

import pandas as pd

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "src"))

from calendar_ics import add_cycle, parse_pay_months, TRI_CYCLE  # noqa: E402 - 월말 유지·위클리 처리 재사용
from classify import prev_business_day, US_MARKET   # noqa: E402
from whitelist import normalize_code        # noqa: E402
import portfolio                            # noqa: E402

WATCH_CSV = BASE / "data" / "reference" / "관심종목.csv"
COLUMNS = ["종목코드", "종목명", "수량", "투자금액", "메모"]

# 분배주기 → 다음 회차 간격 (calendar_ics 와 같은 정의)
VALID_CYCLES = {"위클리", "월", "분기", TRI_CYCLE, "반기", "연"}


def _pay_months(hit) -> list[int] | None:
    """연3회 종목의 지급 달 목록. 그 외 주기는 None (add_cycle 이 무시한다)."""
    cycle = str(hit.get("분배주기") or "").strip()
    return parse_pay_months(hit.get("지급월목록")) if cycle == TRI_CYCLE else None


def empty() -> pd.DataFrame:
    return pd.DataFrame({
        "종목코드": pd.Series(dtype="string"),
        "종목명": pd.Series(dtype="string"),
        "수량": pd.Series(dtype="float"),
        "투자금액": pd.Series(dtype="float"),
        "메모": pd.Series(dtype="string"),
    })


def load(path: Path | None = None) -> pd.DataFrame:
    path = path or WATCH_CSV
    if not path.exists():
        return empty()
    frame = pd.read_csv(path, dtype={"종목코드": str})
    for column in COLUMNS:
        if column not in frame.columns:
            frame[column] = None
    for column in ("수량", "투자금액"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    for column in ("종목코드", "종목명", "메모"):
        frame[column] = frame[column].fillna("").astype(str).str.strip()
    return frame[COLUMNS]


def save(frame: pd.DataFrame, path: Path | None = None) -> Path:
    path = path or WATCH_CSV
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.reindex(columns=COLUMNS).to_csv(path, index=False, encoding="utf-8-sig")
    return path


def mark(df: pd.DataFrame, watch: pd.DataFrame | None = None) -> pd.DataFrame:
    """ETF 표에 '관심'(불리언) 칸을 붙인다 — 코드 우선, 없으면 종목명."""
    watch = load() if watch is None else watch
    out = df.copy()
    out["관심"] = False
    if watch.empty:
        return out

    codes = {normalize_code(c) for c in watch["종목코드"]} - {""}
    names = {str(n).strip() for n in watch["종목명"]} - {""}
    df_code = df["KRX코드"].map(normalize_code) if "KRX코드" in df.columns \
        else pd.Series("", index=df.index)
    df_name = df["종목명"].astype(str).str.strip() if "종목명" in df.columns \
        else pd.Series("", index=df.index)
    out["관심"] = (df_code.isin(codes) & df_code.ne("")) \
        | (df_name.isin(names) & df_code.eq(""))
    # 코드로 못 맞춘 행은 이름으로도 본다 (코드 오타 대비)
    out.loc[df_name.isin(names), "관심"] = True
    return out


def _tax_rate(account_type: str, market: str) -> float:
    """원천징수율 — 미국 상장분은 계좌와 무관하게 현지 15%."""
    if str(market).strip() == US_MARKET:
        return portfolio.US_WITHHOLDING_RATE
    return portfolio.account_tax_rate(account_type, final=False)


def plan(watch: pd.DataFrame, etf_table: pd.DataFrame,
         account_type: str = portfolio.DEFAULT_ACCOUNT_TYPE) -> pd.DataFrame:
    """관심종목마다 '사면 1년에 얼마'를 계산한 표를 돌려준다.

    etf_table: 대시보드 load_data() 결과 (KRX코드·연간주당분배금·1년수익률 포함).
    돌려주는 칸: 종목명·코드·시장·주기·현재가·수량·투자금액·예상연분배금(세전/세후)·
    예상연분배율·1년수익률(참고)·분배금성장률·분배변동계수·다음매수마감·비고
    """
    if watch.empty:
        return pd.DataFrame()

    table = etf_table.copy()
    table["_code"] = table["KRX코드"].map(normalize_code) if "KRX코드" in table.columns else ""
    table["_name"] = table["종목명"].astype(str).str.strip()
    # drop=False: 인덱스로 써도 _code 칸은 남겨 둔다 (아래에서 hit["_code"] 로 읽는다)
    by_code = (table[table["_code"].ne("")]
               .drop_duplicates("_code").set_index("_code", drop=False))
    by_name = table.drop_duplicates("_name").set_index("_name", drop=False)

    # 현재가 — 국내는 KRX 정보, 미국은 us_etf 수집분(원화 환산)
    import etf_info
    import us_etf
    info = etf_info.load_latest()
    kr_price = dict(zip(info["KRX코드"].astype(str).str.strip(),
                        pd.to_numeric(info["종가"], errors="coerce"))) \
        if not info.empty else {}
    us = us_etf.load_latest()
    us_price = dict(zip(us["티커"].astype(str).str.strip(),
                        pd.to_numeric(us["종가"], errors="coerce"))) \
        if not us.empty else {}

    rows = []
    for _, r in watch.iterrows():
        code = normalize_code(r.get("종목코드"))
        name = str(r.get("종목명") or "").strip()
        hit = None
        if code and code in by_code.index:
            hit = by_code.loc[code]
        elif name and name in by_name.index:
            hit = by_name.loc[name]
        if hit is None:
            rows.append({"종목명": name or code, "코드": code, "비고": "목록에 없음"})
            continue

        code = str(hit["_code"] or code)
        market = str(hit.get("상장시장") or "한국").strip()
        price = us_price.get(code) if market == US_MARKET else kr_price.get(code)
        price = float(price) if price is not None and pd.notna(price) else None

        # 수량이 없으면 투자금액 ÷ 현재가 (정수 주). 실제 투자금액을 다시 잡는다
        quantity = pd.to_numeric(r.get("수량"), errors="coerce")
        budget = pd.to_numeric(r.get("투자금액"), errors="coerce")
        note = ""
        if pd.isna(quantity) or not quantity:
            if pd.notna(budget) and budget and price:
                quantity = int(budget // price)
                if quantity == 0:
                    note = "투자금액이 1주 값보다 작음"
            else:
                quantity, note = None, (note or "수량·투자금액 없음")
        invested = (float(quantity) * price) if quantity and price else \
            (float(budget) if pd.notna(budget) else None)

        cycle = str(hit.get("분배주기") or "").strip()
        yearly_per_share = pd.to_numeric(hit.get("연간주당분배금"), errors="coerce")
        payments = pd.to_numeric(hit.get("지급횟수_12개월"), errors="coerce")
        if cycle not in VALID_CYCLES or pd.isna(yearly_per_share) or not payments:
            note = note or "분배 이력 없음(미분배)"

        yearly = float(yearly_per_share) * float(quantity) \
            if quantity and pd.notna(yearly_per_share) else None
        rate = _tax_rate(account_type, market)
        blocked = (market == US_MARKET
                   and str(account_type).strip() in portfolio.US_BLOCKED_ACCOUNTS)
        if blocked:
            note = "이 계좌로는 매수 불가(미국 상장)"

        anchor = pd.to_datetime(hit.get("최근_지급기준일"), errors="coerce")
        next_close = None
        if cycle in VALID_CYCLES and pd.notna(anchor):
            cursor, today = anchor, pd.Timestamp(date.today())
            for k in range(1, 400):
                nxt = add_cycle(anchor, cycle, k, _pay_months(hit))
                if nxt is None:
                    break
                if nxt > today:
                    cursor = nxt
                    break
            back = 1 if market == US_MARKET else 2
            next_close = prev_business_day(cursor, back, market).date() \
                if cursor is not None and cursor > today else None

        rows.append({
            "종목명": str(hit["종목명"]),
            "코드": code,
            "시장": market,
            "주기": cycle or "미분배",
            "현재가": price,
            "수량": float(quantity) if quantity else None,
            "투자금액": invested,
            "예상연분배금": yearly,
            "예상연분배금_세후": yearly * (1 - rate) if yearly is not None else None,
            "예상연분배율": (yearly / invested * 100)
                            if yearly is not None and invested else None,
            "1년수익률": pd.to_numeric(hit.get("1년수익률"), errors="coerce"),
            "분배금성장률": pd.to_numeric(hit.get("분배금성장률"), errors="coerce"),
            # 회차별 금액이 들쭉날쭉하면 '예상연분배금'을 그대로 믿기 어렵다
            "분배변동계수": pd.to_numeric(hit.get("분배변동계수"), errors="coerce"),
            # 5분 루틴 판정 — 관심종목 탭에서 이 칸으로 거른다 (routine.apply_filters)
            **{column: hit.get(column) for column in
               ("루틴점수", "루틴_가격잠식", "루틴_분배안정",
                "루틴_원금반환", "루틴_특별분배", "커버드콜")},
            "다음매수마감": next_close,
            "비고": note,
        })
    return pd.DataFrame(rows)


def schedule(planned: pd.DataFrame, etf_table: pd.DataFrame,
             months: int = 12,
             account_type: str = portfolio.DEFAULT_ACCOUNT_TYPE,
             today: date | None = None) -> pd.DataFrame:
    """관심종목 전체의 회차별 예상 지급 일정 (날짜순).

    1회분 = 연간주당분배금 ÷ 지급횟수 (직전 12개월 평균이 반복된다고 가정).
    돌려주는 칸: 예상기준일·예상매수마감·종목명·주기·세전·세후
    """
    today = today or date.today()
    if planned is None or planned.empty:
        return pd.DataFrame(columns=["예상기준일", "예상매수마감", "종목명", "주기", "세전", "세후"])

    table = etf_table.copy()
    table["_code"] = table["KRX코드"].map(normalize_code) if "KRX코드" in table.columns else ""
    by_code = table[table["_code"].ne("")].drop_duplicates("_code").set_index("_code")

    start = pd.Timestamp(today)
    limit = start + pd.DateOffset(months=months)
    rows = []
    for _, r in planned.iterrows():
        code = normalize_code(r.get("코드"))
        quantity = pd.to_numeric(r.get("수량"), errors="coerce")
        if not code or code not in by_code.index or pd.isna(quantity) or not quantity:
            continue
        hit = by_code.loc[code]
        cycle = str(hit.get("분배주기") or "").strip()
        yearly = pd.to_numeric(hit.get("연간주당분배금"), errors="coerce")
        payments = pd.to_numeric(hit.get("지급횟수_12개월"), errors="coerce")
        anchor = pd.to_datetime(hit.get("최근_지급기준일"), errors="coerce")
        if cycle not in VALID_CYCLES or pd.isna(yearly) or not payments or pd.isna(anchor):
            continue

        market = str(hit.get("상장시장") or "한국").strip()
        per_payment = float(yearly) / float(payments) * float(quantity)
        after = per_payment * (1 - _tax_rate(account_type, market))
        back = 1 if market == US_MARKET else 2

        for k in range(1, 400):
            when = add_cycle(anchor, cycle, k, _pay_months(hit))
            if when is None or when > limit:
                break
            if when <= start:
                continue
            rows.append({
                "예상기준일": when.date(),
                "예상매수마감": prev_business_day(when, back, market).date(),
                "종목명": str(hit["종목명"]),
                "시장": market,
                "주기": cycle,
                "세전": per_payment,
                "세후": after,
            })

    columns = ["예상기준일", "예상매수마감", "종목명", "시장", "주기", "세전", "세후"]
    out = pd.DataFrame(rows)
    if out.empty:
        return pd.DataFrame(columns=columns)
    return out.sort_values("예상기준일").reset_index(drop=True)


# ── 환율 시나리오 ──────────────────────────────────────────
def base_rate() -> float:
    """미국 금액을 원화로 바꿀 때 쓴 환율 (us_etf 수집 시점). 없으면 0."""
    import us_etf

    us = us_etf.load_latest()
    if us.empty or "환율" not in us.columns:
        return 0.0
    rate = pd.to_numeric(us["환율"], errors="coerce").dropna()
    return float(rate.iloc[0]) if len(rate) else 0.0


# 환율을 곱해 다시 계산해야 하는 **금액** 칸. 비율(%)·날짜는 건드리지 않는다
FX_MONEY_COLUMNS = ("현재가", "투자금액", "예상연분배금", "예상연분배금_세후",
                    "세전", "세후")


def apply_fx(frame: pd.DataFrame, new_rate: float,
             old_rate: float | None = None) -> pd.DataFrame:
    """미국 상장 종목의 원화 금액을 다른 환율로 다시 계산한다.

    국내 종목은 손대지 않는다. 비율(%) 칸도 그대로다 —
    투자금액과 분배금이 같은 비율로 움직여서 **분배율은 환율과 무관**하다.
    """
    old_rate = base_rate() if old_rate is None else old_rate
    if frame.empty or not new_rate or not old_rate or "시장" not in frame.columns:
        return frame
    ratio = float(new_rate) / float(old_rate)
    if abs(ratio - 1.0) < 1e-9:
        return frame

    out = frame.copy()
    is_us = out["시장"].astype(str).str.strip() == US_MARKET
    for column in FX_MONEY_COLUMNS:
        if column in out.columns:
            values = pd.to_numeric(out[column], errors="coerce")
            out.loc[is_us, column] = values[is_us] * ratio
    return out


def fx_total_return(planned: pd.DataFrame, new_rate: float,
                    old_rate: float | None = None) -> dict | None:
    """'지금 환율로 사고 **난 뒤** 환율이 움직이면' 의 1년 환율 효과 (미국 종목만).

    apply_fx 와 보는 상황이 다르다 — apply_fx 는 "가정 환율로 **산다**"라서
    원금과 분배금이 같이 움직여 분배율이 그대로다. 여기서는 원금이 **오늘
    환율로 이미 나간 뒤**라, 환율이 움직이면 원금에 환차손익이 생긴다.
    가격 변동은 없다고 가정하고 환율 효과만 떼어 본다. 분배금은 세후 기준.

    돌려주는 값: 투자금액·환차손익·연분배금(전/후)·총효과. 미국 종목이 없거나
    환율을 모르면 None — 0 으로 채우면 '효과 없음'과 구분이 안 된다.
    """
    old_rate = base_rate() if old_rate is None else old_rate
    if planned.empty or not new_rate or not old_rate \
            or "시장" not in planned.columns:
        return None
    us = planned[planned["시장"].astype(str).str.strip() == US_MARKET]
    invested = pd.to_numeric(us.get("투자금액"), errors="coerce").sum()
    dividend = pd.to_numeric(us.get("예상연분배금_세후"), errors="coerce").sum()
    if us.empty or not invested:
        return None
    ratio = float(new_rate) / float(old_rate)
    fx_gain = invested * (ratio - 1.0)          # 원금에 생기는 환차손익
    div_after = dividend * ratio                # 분배금은 새 환율로 받는다
    return {
        "투자금액": float(invested),
        "환차손익": float(fx_gain),
        "연분배금_전": float(dividend),
        "연분배금_후": float(div_after),
        # 1년 뒤 원화 손익 (가격 변동 제외) vs 환율이 그대로일 때
        "총효과": float(fx_gain + div_after),
        "총효과_환율_그대로": float(dividend),
    }
