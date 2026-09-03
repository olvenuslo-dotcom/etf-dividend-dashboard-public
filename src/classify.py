# -*- coding: utf-8 -*-
"""
1단계-분류: 수집한 원본 CSV를 읽어 종목(ISIN)별로 분배주기 등을 분류하고 엑셀로 저장
- 청산분배 건은 제외 (정상적인 정기 분배만 대상)
- 분배주기: 최근 12개월 '지급 횟수' 기준 (CLAUDE.md 규칙)
  * 참고용으로 '지급 간격 중앙값'도 함께 계산해, 두 방식이 다르면 비고에 표시
- 국내형/해외형: 종목명이 아니라 '유형' 데이터값으로 판단
- 매수마감일: 지급기준일의 2영업일 전 (주말 + 한국 공휴일 제외)
결과: output/분배ETF목록.xlsx  (시트1=전체, 시트2=월·위클리만)
"""
from pathlib import Path
import re
import sys
import pandas as pd
import holidays

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _abbr(word: str) -> str:
    """앞뒤로 영문이 안 붙은 낱말만 (예: 'TIPS' 는 잡고 'TIPSY' 는 안 잡음)."""
    return rf"(?<![A-Za-z]){word}(?![A-Za-z])"

BASE = Path(__file__).resolve().parent.parent
RAW_DIR = BASE / "data" / "raw"
OUT_DIR = BASE / "output"
OUT_DIR.mkdir(parents=True, exist_ok=True)

KR_HOLIDAYS = holidays.SouthKorea()   # 한국 공휴일
US_HOLIDAYS = holidays.UnitedStates()  # 미국 공휴일 (미국 상장 ETF 날짜 계산용)

# 세이브로에 분배 이력이 아예 없는 ETF를 부르는 이름 (후보군으로만 본다)
NO_CYCLE = "미분배"

# KRX에서 붙여 오는 칸들 (etf_info.py 미실행이면 비어 있다)
KRX_COLUMNS = ["종목코드", "시가총액", "총보수", "자산분류", "시장분류", "운용방식",
               "과세유형", "기초지수명", "투자지역", "테마섹터", "전략",
               # 상장일: 국내 상장폐지 요건이 '상장 1년 초과'부터 걸리기 때문에 필요하다
               "상장일"]

OUTPUT_COLUMNS = [
    # 종목코드: 국내는 KRX 6자리, 미국은 티커(SCHD). 보유종목·화이트리스트를 맞출 때 쓴다
    "종목명", "종목코드", "운용사", "유형", "국내해외", "상장시장", "시가총액", "총보수",
    "자산분류", "시장분류", "투자지역", "테마섹터", "전략", "운용방식", "과세유형",
    "분배주기", "지급횟수_12개월", "중앙값간격_일",
    # 연3회 종목만 채워진다 (예: "4,7,10") — 캘린더가 이 달로 다음 회차를 예측한다
    "지급월목록",
    "최근_주당분배금", "최근_분배율",
    # 직전 회차 분배금 ÷ 지금 시장가 (세이브로 분배율은 기준가 기준이라 따로 둔다)
    "최근분배율_시장가",
    # 연간(TTM) — 주기가 다른 종목끼리는 이쪽으로 비교해야 한다
    "연간주당분배금", "연간분배율", "분배금성장률", "분배변동계수",
    # 특별분배(한 회차만 크게 준 것)를 뺀 '평상시' 분배율 — 회차 중앙값 기준
    "중앙값연주당분배금", "중앙값연분배율",
    # 장기 성과·위험 — 1년만 보면 사이클을 못 본다 (미국 상장분만 채워진다)
    "3년평균수익률", "5년평균수익률", "베타", "괴리율", "설정일", "운용기간_년",
    # PTP = 팔 때 매도대금의 10% 가 원천징수된다 (손실이 나도 뗀다)
    "PTP", "PTP면제만료",
    # 규모가 작으면 운용사가 접는다 → 원치 않는 시점에 강제 청산된다
    "일평균거래대금", "청산위험",
    "최근_지급기준일", "최근_실지급일", "최근_매수마감일",
    "기초지수명", "비고", "비고상세", "ISIN",
]

# 상장시장 — 어느 거래소에 상장돼 있나 (투자 대상 기준인 '국내해외'와 다르다)
KR_MARKET, US_MARKET = "한국", "미국"

# 연 3회 지급 (예: 4·7·10월) — 국내 지수형에 흔한 주기인데 5분류에 자리가 없어
# '주기 확인'으로 새던 것 (37단계). 횟수·간격이 아니라 **지급 달**로 확인해 붙인다
TRI_CYCLE = "연3회"

# 자주 지급하는 순서 (비고 원인 추정에 사용)
# '미분배'는 분배 이력이 아예 없는 종목 — 판정이 아니라 '자료 없음'이라 맨 뒤에 둔다
CYCLE_RANK = {"위클리": 0, "월": 1, "분기": 2, TRI_CYCLE: 3, "반기": 4, "연": 5,
              NO_CYCLE: 6}

# 운용사명 끝의 회사 형태 표기 — 화면을 좁게 쓰려고 떼어낸다
MANAGER_SUFFIX = re.compile(r"(자산운용|신탁운용|운용)$")


def short_manager(name) -> str:
    """운용사명을 짧게. 예) 미래에셋자산운용→미래에셋, 한국투자신탁운용→한국투자."""
    text = str(name).strip()
    short = MANAGER_SUFFIX.sub("", text).strip()
    return short or text   # 전부 잘려나가면 원래 이름 유지


def latest_raw_csv() -> Path:
    """data/raw 에서 가장 최근에 저장된 수집 CSV 찾기."""
    files = sorted(RAW_DIR.glob("seibro_분배금_*.csv"))
    if not files:
        raise FileNotFoundError("data/raw 에 수집 CSV가 없습니다. 먼저 collect.py 를 실행하세요.")
    return files[-1]


def cycle_by_count(n: int) -> str:
    """최근 12개월 지급 '횟수'로 분배주기 판정 (기하 중간값 기준 구간).
    분류 5종: 위클리 / 월 / 분기 / 반기 / 연 (격주는 사용 안 함)."""
    if n >= 40:
        return "위클리"   # 실제 주간분배(연 약 52회)
    if n >= 7:
        return "월"       # 7~39회 (신규상장·불규칙 포함, 비고에서 간격으로 교차검증)
    if n >= 3:
        return "분기"
    if n == 2:
        return "반기"
    return "연"          # n == 1


def tri_annual_months(recent_dates, previous_dates) -> list[int] | None:
    """두 해 연속 **같은 달 3곳**에서만 지급했으면 그 달 목록(정렬), 아니면 None.

    한 해만 봐서는 분기(4회)에서 한 회 빠진 것과 구분이 안 된다 —
    앞 해에도 똑같은 3개 달이었을 때만 '연 3회 주기'로 인정한다.
    이름을 보고 추측하는 것이 아니라 지급 기록으로만 판단한다.
    """
    recent_months = sorted(set(recent_dates.dt.month))
    previous_months = sorted(set(previous_dates.dt.month))
    if (len(recent_dates) == 3 and len(previous_dates) == 3
            and len(recent_months) == 3 and recent_months == previous_months):
        return recent_months
    return None


def cycle_by_interval(days: float) -> str:
    """지급 '간격(일)'로 분배주기 판정 (참고용 교차검증)."""
    if days is None:
        return ""
    if days <= 10:
        return "위클리"
    if days <= 45:
        return "월"
    if days <= 100:
        return "분기"
    if days <= 250:
        return "반기"
    return "연"


# ── 분배금 변동계수 ────────────────────────────────────────
# 회차마다 주는 금액이 얼마나 들쭉날쭉한가 = 표준편차 ÷ 평균 (%).
#
# 왜 보나: 분배금의 일부가 **원금 반환(ROC)** 이면 벌어서 주는 게 아니라 내 돈을
# 돌려주는 것인데, 그 공시(19a-1·8937)는 종목마다 PDF라 값으로 받아올 수 없다.
# 벌어서 주는 돈(이자·배당)은 회차마다 비슷하고, 원금을 헐거나 옵션 프리미엄에
# 기대는 돈은 회차마다 출렁인다 → 이 값을 **대용 신호**로 쓴다.
#
# ⚠ 높다고 곧 ROC 라는 뜻이 아니다. 커버드콜은 시장 변동성에 따라 프리미엄이
#   달라져 정상적으로도 출렁이고, 주기를 바꿨거나 연말 특별분배가 끼어도 튄다.
#   판정이 아니라 **들여다볼 종목을 고르는 값**이다.
#
# 평균으로 나누기 때문에 금액 크기·통화와 무관하다 → 원화 환산된 미국 상장분도
# 같은 자로 잰다.
CV_MIN_PAYMENTS = 3       # 2회로는 표준편차가 의미 없다


def variation_coefficient(amounts, min_payments: int = CV_MIN_PAYMENTS) -> float | None:
    """분배금 변동계수(%). 지급 횟수가 모자라거나 평균이 0 이하면 None."""
    values = pd.to_numeric(pd.Series(list(amounts), dtype="object"),
                           errors="coerce").dropna()
    if len(values) < min_payments:
        return None
    mean = float(values.mean())
    if mean <= 0:
        return None
    return round(float(values.std(ddof=1)) / mean * 100, 1)


def median_annual(amounts) -> float | None:
    """**특별분배를 뺀** 연 분배금 = 회차 금액의 중앙값 × 지급 횟수.

    한 회차만 크게 준 특별분배가 끼면 합계(= 연간주당분배금)는 뛰지만 중앙값은
    안 뛴다. 그래서 이 값으로 낸 분배율이 '평상시에 이만큼 나온다'에 가깝다.
    표시된 분배율이 특별분배 한 방으로 부풀려졌는지 보려면 둘을 견주면 된다.

    지급이 1회뿐이어도 낼 수 있다(중앙값 = 그 값). 변동계수와 달리 표본 수
    제한을 두지 않는 이유다 — 특별분배 판별이 아니라 '평상 수준'을 재는 값이다.
    """
    values = pd.to_numeric(pd.Series(list(amounts), dtype="object"),
                           errors="coerce").dropna()
    if values.empty:
        return None
    return float(values.median()) * len(values)


def prev_business_day(d: pd.Timestamp, n: int = 2, market: str = KR_MARKET) -> pd.Timestamp:
    """d 에서 n 영업일 전 날짜 (주말·공휴일 제외). 공휴일은 상장시장에 맞춰 본다."""
    if pd.isna(d):
        return pd.NaT
    days_off = US_HOLIDAYS if market == US_MARKET else KR_HOLIDAYS
    cur = d
    left = n
    while left > 0:
        cur = cur - pd.Timedelta(days=1)
        if cur.weekday() < 5 and cur.date() not in days_off:   # 평일 & 비공휴일
            left -= 1
    return cur


def classify(df: pd.DataFrame) -> pd.DataFrame:
    # 1) 자료형 정리
    df = df.copy()
    if df.empty and not len(df.columns):
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    required = {
        "ISIN", "종목명", "운용사", "유형", "지급기준일", "실지급일",
        "주당분배금", "배당구분",
    }
    missing = required.difference(df.columns)
    if missing:
        raise ValueError(f"분류 원본 컬럼이 부족합니다: {', '.join(sorted(missing))}")
    df["지급기준일"] = pd.to_datetime(df["지급기준일"], format="%Y%m%d", errors="coerce")
    df["실지급일"] = pd.to_datetime(df["실지급일"], format="%Y%m%d", errors="coerce")
    # 구 수집본은 BUNBE(분배율)를 '주당분배금', ESTM_STDPRC(실제 금액)를
    # '결산과표기준가'로 잘못 명명했다. 기존·신규 CSV를 모두 지원한다.
    if "결산과표기준가" in df.columns:
        amount_column, rate_column = "결산과표기준가", "주당분배금"   # 구 수집본
    else:
        amount_column, rate_column = "주당분배금", "분배율"           # 신 수집본
    # 분배율은 세이브로가 주는 값(분배금 ÷ 기준가, 단위 %)을 그대로 쓴다
    rate = df[rate_column] if rate_column in df.columns else None
    df["분배율"] = (pd.to_numeric(
        rate.astype(str).str.replace(",", "", regex=False), errors="coerce")
        if rate is not None else pd.NA)
    df["주당분배금"] = pd.to_numeric(
        df[amount_column].astype(str).str.replace(",", "", regex=False), errors="coerce")

    # 2) 청산분배 제외 → 정기 분배(이익분배)만 남김
    df = df[df["배당구분"] == "이익분배"].copy()

    # 3) 종목(ISIN)별 집계
    records = []
    for isin, g in df.groupby("ISIN"):
        g = g.dropna(subset=["지급기준일"]).sort_values("지급기준일")
        if g.empty:
            continue

        # ⚠ 수집분이 2년치라 **최근 12개월로 잘라서** 판정해야 한다.
        # 자르지 않으면 분기배당이 8회로 세어져 '월'로 뒤집힌다(7~39회가 월).
        last_day = g["지급기준일"].iloc[-1]
        recent = g[g["지급기준일"] > last_day - pd.DateOffset(years=1)]
        previous = g[(g["지급기준일"] > last_day - pd.DateOffset(years=2))
                     & (g["지급기준일"] <= last_day - pd.DateOffset(years=1))]

        dates = recent["지급기준일"]
        n = len(dates)
        if n == 0:
            continue

        # 지급 간격 중앙값(일) — 지급이 2번 이상일 때만 계산
        if n >= 2:
            gaps = dates.diff().dropna().dt.days
            median_gap = float(gaps.median())
        else:
            median_gap = None

        cyc_cnt = cycle_by_count(n)          # 횟수 기준 (주 판정)
        cyc_int = cycle_by_interval(median_gap)  # 간격 기준 (참고)

        # 연 3회(예: 4·7·10월)는 횟수로는 분기, 간격으로는 반기가 나와 늘 갈린다.
        # 두 해의 지급 달이 같은 3곳이면 확인이 끝난 것 → 주기를 확정하고 비고를 안 단다
        pay_months = tri_annual_months(dates, previous["지급기준일"])
        if pay_months:
            cyc_cnt = TRI_CYCLE

        # 두 방식이 다르면 '분배주기' 값이 틀렸을 수 있다는 뜻 → 무엇을 확인할지 적어둔다
        note, note_detail = "", ""
        if cyc_int and cyc_int != cyc_cnt and cyc_cnt != TRI_CYCLE:
            note = "주기 확인"
            cause = ("상장한 지 얼마 안 돼 12개월치 지급 횟수가 모자란 것으로 보임"
                     if CYCLE_RANK[cyc_int] < CYCLE_RANK[cyc_cnt]
                     else "지급 간격이 들쭉날쭉해 주기가 일정하지 않음")
            note_detail = (f"확인할 항목: 분배주기. "
                           f"지급 횟수로는 {n}회→{cyc_cnt}, 지급 간격으로는 "
                           f"{median_gap:.0f}일→{cyc_int}로 판정이 갈림. {cause}.")

        last = recent.iloc[-1]   # 가장 최근 지급 건
        유형 = str(last["유형"])
        # 최근 12개월 분배금 합(TTM) — 주기가 다른 종목끼리 비교하려면 이 값이 있어야 한다.
        # 분배율(%)은 KRX 종가를 붙인 뒤에야 낼 수 있어 attach_krx_info 에서 계산한다.
        ttm = float(recent["주당분배금"].sum())
        # 그 앞 12개월과 견줘 분배금이 늘었는지 (2년치를 받아야 나온다)
        before = float(previous["주당분배금"].sum())
        growth = round((ttm - before) / before * 100, 2) if before > 0 else None
        records.append({
            "종목명": last["종목명"],
            "운용사": short_manager(last["운용사"]),
            "유형": 유형,
            "국내해외": "해외" if "해외" in 유형 else "국내",
            "상장시장": KR_MARKET,
            # KRX 값들 — attach_krx_info() 에서 채운다
            **{column: None for column in KRX_COLUMNS},
            "분배주기": cyc_cnt,
            "지급횟수_12개월": n,
            "중앙값간격_일": round(median_gap, 1) if median_gap is not None else None,
            "지급월목록": ",".join(map(str, pay_months)) if pay_months else None,
            "최근_주당분배금": last["주당분배금"],
            "최근_분배율": last["분배율"] if pd.notna(last["분배율"]) else None,
            "연간주당분배금": ttm or None,
            "연간분배율": None,        # KRX 종가를 붙인 뒤 채운다
            "분배금성장률": growth,    # 앞 12개월 자료가 없으면 None
            # 회차별 금액이 얼마나 들쭉날쭉한가 (ROC 대용 신호). 3회 미만이면 None
            "분배변동계수": variation_coefficient(recent["주당분배금"]),
            # 특별분배를 뺀 평상 수준. 분배율은 KRX 종가를 붙인 뒤 계산한다
            "중앙값연주당분배금": median_annual(recent["주당분배금"]),
            "중앙값연분배율": None,
            "최근_지급기준일": last["지급기준일"].date() if pd.notna(last["지급기준일"]) else None,
            "최근_실지급일": last["실지급일"].date() if pd.notna(last["실지급일"]) else None,
            "최근_매수마감일": prev_business_day(last["지급기준일"], 2).date()
                              if pd.notna(last["지급기준일"]) else None,
            "비고": note,
            "비고상세": note_detail,
            "ISIN": isin,
        })

    result = pd.DataFrame(records, columns=OUTPUT_COLUMNS)
    if result.empty:
        return result

    # 보기 좋게 정렬: 분배주기(자주 지급하는 순) → 종목명
    result["_ord"] = result["분배주기"].map(CYCLE_RANK)
    result = result.sort_values(["_ord", "종목명"]).drop(columns="_ord").reset_index(drop=True)
    return result


def add_undistributed(result: pd.DataFrame, info: pd.DataFrame) -> pd.DataFrame:
    """세이브로에 분배 이력이 없는 KRX 상장 ETF를 '미분배'로 덧붙인다.

    신규 상장이라 아직 안 준 것과 원래 분배를 안 하는 것이 섞여 있다.
    분배 관련 칸은 전부 비우고, 종목명·운용사·분류는 KRX 값 그대로 쓴다.
    """
    if info.empty or "종목명" not in info.columns:
        return result       # 옛 KRX CSV(종목명 없음)면 아무것도 안 붙인다

    have = set(result["ISIN"].astype(str))
    rest = info[~info["ISIN"].astype(str).isin(have)].copy()
    rest = rest[rest["종목명"].astype(str).str.strip() != ""]
    if rest.empty:
        return result

    rows = pd.DataFrame({
        "종목명": rest["종목명"].astype(str).str.strip(),
        "운용사": rest["운용사"].map(short_manager) if "운용사" in rest.columns else "",
        "유형": "",
        # 국내/해외는 KRX 시장분류 값으로 본다 (이름으로 추정하지 않는다)
        "국내해외": rest.get("시장분류", pd.Series("", index=rest.index))
                    .map(lambda v: "해외" if str(v).strip() == "해외" else "국내"),
        "상장시장": KR_MARKET,
        "분배주기": NO_CYCLE,
        "지급횟수_12개월": 0,
        "중앙값간격_일": None,
        "최근_주당분배금": None,
        "최근_분배율": None,
        "연간주당분배금": None,
        "연간분배율": None,
        "분배금성장률": None,
        "분배변동계수": None,
        "중앙값연주당분배금": None,
        "중앙값연분배율": None,
        "최근_지급기준일": None,
        "최근_실지급일": None,
        "최근_매수마감일": None,
        "비고": "",
        "비고상세": "",
        "ISIN": rest["ISIN"].astype(str),
    })
    for column in KRX_COLUMNS:          # 시총·총보수·분류는 KRX 값 그대로
        rows[column] = rest[column].values if column in rest.columns else None
    # 상장일 → 설정일 (미국 '설정일'과 같은 칸에 모은다)
    if "상장일" in rows.columns:
        listed = rows["상장일"].astype(str).str.strip().replace(
            {"nan": "", "None": "", "NaT": ""})
        rows["설정일"] = listed
        rows["운용기간_년"] = listed.map(years_since)

    # reindex 로 빠진 칸은 빈 값으로 채운다. 여기 없는 칸(미국 상장분 전용 지표 등)을
    # OUTPUT_COLUMNS 에 새로 넣을 때마다 KeyError 로 미분배 종목이 통째로 빠지곤 했다.
    rows = rows.reindex(columns=OUTPUT_COLUMNS)
    # 값이 하나도 없는 칸(분배 관련)은 원래 표의 자료형에 맞춰 둔다 — concat 경고 방지
    for column in rows.columns:
        if rows[column].isna().all() and column in result.columns:
            rows[column] = rows[column].astype(result[column].dtype)

    merged = pd.concat([result, rows], ignore_index=True)
    merged["_ord"] = merged["분배주기"].map(CYCLE_RANK)
    return (merged.sort_values(["_ord", "종목명"])
            .drop(columns="_ord").reset_index(drop=True))


# 미국 ETF 자산분류 — 종목명·카테고리 문구로 판단 (yfinance 가 주는 값 그대로 씀)
US_ASSET_RULES = [
    ("채권", r"Bond|Treasury|Muni|Corporate|Fixed Income|Aggregate|" + _abbr("TIPS")),
    ("원자재", r"Commodit|Gold|Silver|Copper|Oil|Gas|Precious|Metal"),
    ("부동산", r"Real Estate|REIT|Realty"),
    ("통화", r"Currency|Dollar|" + _abbr("FX")),
    ("혼합자산", r"Allocation|Balanced|Multi-?Asset|Target Date"),
]
# 미국 상장 ETF 배당은 현지에서 15% 떼고 들어온다 (국내 추가 원천징수 없음)
US_TAX_TYPE = "해외상장(미국 15% 원천징수)"


# ── 청산(상장폐지) 위험 ────────────────────────────────────
# 규모가 작은 ETF는 접힌다. 그러면 원치 않는 시점에 강제로 현금화된다.
#
# ⚠ 두 시장은 기준의 **성격이 다르다.** 같은 '위험' 글자라도 뜻이 같지 않다.
#
# [국내] 실제 상장폐지 규정이다 — 요건에 걸리면 강제로 폐지된다.
#   · 순자산총액 50억원 미만이 1개월 이상 지속
#   · 상장 1년이 지난 ETF가 50억원 미만이 되고 다음 반기말까지 미해소
#   · 신탁원본액 50억원 미만이 3개월간 미해소
#   (이 밖에 LP 부재, 상관계수 미달(패시브 0.9·액티브 0.7) 요건도 있으나
#    그 자료는 아직 수집하지 않아 여기 반영되지 않는다.)
#
# [미국] 규정이 아니라 **운용사 재량**이다.
#   · 거래소(NYSE Arca) 상장유지 요건은 시가총액 $1M·주주 50인으로 훨씬 낮다
#   · $50M 은 스폰서들이 통상 손익분기로 잡는 선이고, 그 아래면 청산이 잦다
#   즉 미국의 '위험'은 규정 위반이 아니라 **접힐 확률이 높다**는 뜻이다.
#
# 어느 쪽이든 **참고 신호이지 예측이 아니다.**
KR_LIQUIDATION_RISK = 50e8        # 50억원 — 실제 상장폐지 요건
KR_LIQUIDATION_WATCH = 100e8      # 100억원 — 요건에 근접
US_LIQUIDATION_RISK_USD = 50e6    # $50M — 업계 통상 손익분기
US_LIQUIDATION_WATCH_USD = 100e6  # $100M
US_DELIST_FLOOR_USD = 1e6         # $1M — 거래소 상장유지 요건(규정)

RISK_LEVEL, WATCH_LEVEL = "위험", "주의"
DELIST_LEVEL = "상폐요건"          # 규정선 아래 — 국내 50억 / 미국 $1M


def liquidation_risk(net_assets, market, rate: float | None = None,
                     listed_on=None) -> pd.Series:
    """순자산으로 청산 위험을 매긴다. 시장마다 기준의 성격이 달라 따로 본다.

    net_assets: 원화 환산된 순자산(시가총액) 열
    market: 상장시장 열 ('한국'/'미국')
    rate: 원/달러. 미국 기준($)을 원화로 바꿀 때 쓴다. 없으면 미국은 판정하지 않는다.
    listed_on: 상장일 열. 국내 요건은 **상장 1년 초과**부터 걸리므로, 주면
        갓 상장한 ETF를 '상폐요건'에서 빼고 '주의'로 낮춘다.

    돌려주는 값: '상폐요건' / '위험' / '주의' / '' (넷 다 위 상수 참고)
    """
    value = pd.to_numeric(net_assets, errors="coerce")
    is_us = market.astype(str).str.strip() == US_MARKET
    risk = pd.Series(pd.NA, index=value.index, dtype=object)

    # ── 국내 — 50억원이 곧 상장폐지 요건선이다 (규정)
    kr = ~is_us & value.notna()
    risk[kr & (value < KR_LIQUIDATION_RISK)] = DELIST_LEVEL
    risk[kr & (value >= KR_LIQUIDATION_RISK)
         & (value < KR_LIQUIDATION_WATCH)] = WATCH_LEVEL

    # 상장 1년이 안 된 국내 ETF는 아직 요건 대상이 아니다 → 한 단계 낮춘다
    if listed_on is not None:
        listed = pd.to_datetime(listed_on, errors="coerce")
        young = kr & listed.notna() & (
            (pd.Timestamp.today() - listed).dt.days < 365)
        risk[young & (risk == DELIST_LEVEL)] = WATCH_LEVEL

    # ── 미국 — $1M 만 규정선이고, $50M 은 운용사가 접는 관행선이다
    if rate:
        us = is_us & value.notna()
        risk[us & (value < US_LIQUIDATION_RISK_USD * rate)] = RISK_LEVEL
        risk[us & (value >= US_LIQUIDATION_RISK_USD * rate)
             & (value < US_LIQUIDATION_WATCH_USD * rate)] = WATCH_LEVEL
        # 규정선 아래는 관행선보다 더 급하다 → 마지막에 덮어쓴다
        risk[us & (value < US_DELIST_FLOOR_USD * rate)] = DELIST_LEVEL

    return risk.fillna("")


def years_since(inception) -> float | None:
    """설정일부터 오늘까지 몇 년인가. 신생 ETF를 가려내는 데 쓴다.

    3·5년 수익률이 빈 게 **아직 그만큼 안 굴러서인지** 데이터가 없어서인지
    이 값이 있어야 구분된다.
    """
    text = str(inception or "").strip()
    if not text:
        return None
    stamp = pd.to_datetime(text, errors="coerce")
    if pd.isna(stamp):
        return None
    return round((pd.Timestamp.today() - stamp).days / 365.25, 1)


def us_asset_class(text: str) -> str:
    """미국 ETF 자산분류. 규칙에 안 걸리면 주식으로 본다(ETF 대다수가 주식형)."""
    for label, pattern in US_ASSET_RULES:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return label
    return "주식"


def us_dividend_stats() -> dict[str, dict]:
    """미국 상장분의 분배 이력 지표 — **이미 받아 둔 배당 이력**에서 낸다(재수집 없음).

    티커마다 {"분배변동계수", "중앙값연주당분배금"} 을 돌려준다.

    이력의 금액은 수집 당시 환율로 원화 환산돼 있지만, 변동계수는 평균으로
    나누므로 환산 배수가 통째로 약분된다 → 국내와 같은 자로 비교된다.

    ⚠ 창을 **수집일 기준**으로 잡는다(이력 전체의 마지막 배당락일 ≒ 수집일).
    종목마다 자기 마지막 지급일부터 12개월을 세면, 반년 전에 마지막으로 준
    종목은 창이 과거로 밀려 `지급횟수_12개월`(us_etf 가 수집일 기준으로 낸 값)과
    **다른 구간**을 보게 된다 — 표에서 '횟수 2회인데 변동계수가 있다'가 된다.
    국내는 classify() 가 주기 판정과 이 값에 같은 `recent` 를 써서 문제가 없다.
    """
    import us_etf

    history = us_etf.load_dividends()
    if history.empty:
        return {}
    year_ago = history["배당락일"].max() - pd.DateOffset(years=1)
    recent = history[history["배당락일"] > year_ago]
    result: dict[str, dict] = {}
    for ticker, g in recent.groupby("티커"):
        result[str(ticker).strip()] = {
            "분배변동계수": variation_coefficient(g["주당분배금"]),
            "중앙값연주당분배금": median_annual(g["주당분배금"]),
        }
    return result


def add_us_etfs(result: pd.DataFrame, us: pd.DataFrame,
                min_turnover: float = 1_000_000.0) -> pd.DataFrame:
    """미국 직접 상장 ETF(us_etf.py 수집분)를 같은 표에 합친다.

    금액 칸은 us_etf.py 에서 이미 **원화로 환산**해 뒀다(한 표에서 비교하려면 통화가 같아야 한다).
    분배주기 판정은 국내와 똑같은 규칙(최근 12개월 지급 횟수)을 쓴다.
    ISIN 은 비워 둔다 — KRX NAV 조회 대상이 아니라서 그 탭에서 자동으로 빠진다.

    min_turnover: 일평균 거래대금(달러) 하한. 미국은 상장 ETF가 5천 개가 넘어서
    이 선을 안 두면 거의 안 팔리는 종목까지 다 들어온다.
    """
    import etf_taxonomy

    if us.empty:
        return result
    if "일평균거래대금" in us.columns and min_turnover:
        us = us[pd.to_numeric(us["일평균거래대금"], errors="coerce")
                .fillna(0) >= min_turnover]

    stats = us_dividend_stats()   # 배당 이력 파일이 없으면 빈 dict

    rows = []
    for _, r in us.iterrows():
        n = int(r.get("지급횟수_12개월") or 0)
        stat = stats.get(str(r["티커"]).strip(), {})
        median_amount = stat.get("중앙값연주당분배금")
        # 분배율(%)은 같은 환율로 환산된 종가로 나눈다 → 환산 배수가 약분된다
        close = pd.to_numeric(pd.Series([r.get("종가")]), errors="coerce").iloc[0]
        median_rate = (round(median_amount / float(close) * 100, 2)
                       if median_amount and pd.notna(close) and close > 0 else None)
        gap = r.get("중앙값간격_일")
        gap = float(gap) if pd.notna(gap) else None
        cyc_cnt = cycle_by_count(n) if n else NO_CYCLE
        cyc_int = cycle_by_interval(gap)

        note, note_detail = "", ""
        if n and cyc_int and cyc_int != cyc_cnt:
            note = "주기 확인"
            cause = ("상장한 지 얼마 안 돼 12개월치 지급 횟수가 모자란 것으로 보임"
                     if CYCLE_RANK[cyc_int] < CYCLE_RANK[cyc_cnt]
                     else "지급 간격이 들쭉날쭉해 주기가 일정하지 않음")
            note_detail = (f"확인할 항목: 분배주기. 지급 횟수로는 {n}회→{cyc_cnt}, "
                           f"지급 간격으로는 {gap:.0f}일→{cyc_int}로 판정이 갈림. {cause}.")

        # 지역·섹터·전략은 국내와 같은 규칙에 태운다 (종목명 + 야후 카테고리를 근거 문구로)
        basis = f"{r.get('종목명', '')} {r.get('카테고리', '')}".strip()
        region = etf_taxonomy.guess_region(basis, "해외")
        if region in ("", "기타"):
            region = "미국"      # 미국 상장이고 다른 지역 단서가 없으면 미국물로 본다

        기준일 = r.get("최근_지급기준일")
        기준일 = pd.Timestamp(기준일) if pd.notna(기준일) else pd.NaT
        rows.append({
            # 티커는 '종목코드' 칸에 따로 둔다 (표에서 종목명 옆 칸으로 보인다)
            "종목명": str(r.get("종목명") or r["티커"]).strip(),
            "종목코드": str(r["티커"]).strip(),
            # 값이 없으면 NaN 이 오는데 float('nan') 은 truthy 라 'nan' 글자가 새어 나온다
            "운용사": (str(r["운용사"]).strip()
                       if pd.notna(r.get("운용사")) else ""),
            "유형": "",
            "국내해외": "해외",
            "상장시장": US_MARKET,
            "시가총액": r.get("시가총액"),
            "총보수": r.get("총보수"),
            "자산분류": us_asset_class(basis),
            "시장분류": "해외",
            "투자지역": region,
            "테마섹터": etf_taxonomy.guess_sector(basis),
            "전략": etf_taxonomy.guess_strategy(basis),
            "운용방식": "",
            "과세유형": US_TAX_TYPE,
            "분배주기": cyc_cnt,
            "지급횟수_12개월": n,
            "중앙값간격_일": gap,
            "최근_주당분배금": r.get("최근_주당분배금"),
            "최근_분배율": r.get("최근_분배율"),
            # 직전 회차 분배금 ÷ **지금 시장가** — 세이브로 분배율(기준가 기준)과
            # 다르다. 같은 환율로 환산된 값끼리 나눠 환산 배수가 약분된다
            "최근분배율_시장가": (
                round(float(r.get("최근_주당분배금")) / float(close) * 100, 2)
                if pd.notna(pd.to_numeric(pd.Series([r.get("최근_주당분배금")]),
                                          errors="coerce").iloc[0])
                and pd.notna(close) and close > 0 else None),
            "연간주당분배금": r.get("연간주당분배금"),
            "연간분배율": r.get("연간분배율"),
            "분배금성장률": r.get("분배금성장률"),
            "분배변동계수": stat.get("분배변동계수"),
            "중앙값연주당분배금": median_amount,
            "중앙값연분배율": median_rate,
            "3년평균수익률": r.get("3년평균수익률"),
            "5년평균수익률": r.get("5년평균수익률"),
            "베타": r.get("베타"),
            "괴리율": r.get("괴리율"),
            "설정일": str(r.get("설정일") or "").strip(),
            "운용기간_년": years_since(r.get("설정일")),
            "일평균거래대금": r.get("일평균거래대금"),
            "최근_지급기준일": 기준일.date() if pd.notna(기준일) else None,
            "최근_실지급일": None,          # 야후는 지급일을 따로 주지 않는다
            # 야후가 주는 날짜는 **배당락일(ex-date)** 이다. 그날 사면 못 받으므로
            # 매수마감 = 배당락 1영업일 전(미국 공휴일 기준). 국내(T-2)와 규칙이 다르다.
            "최근_매수마감일": (prev_business_day(기준일, 1, US_MARKET).date()
                                if pd.notna(기준일) else None),
            "기초지수명": "",
            "비고": note,
            "비고상세": note_detail,
            "ISIN": "",
        })

    merged = pd.concat([result, pd.DataFrame(rows, columns=OUTPUT_COLUMNS)],
                       ignore_index=True)
    merged = attach_ptp(merged)
    merged["_ord"] = merged["분배주기"].map(CYCLE_RANK)
    return (merged.sort_values(["_ord", "종목명"])
            .drop(columns="_ord").reset_index(drop=True))


def attach_liquidation_risk(result: pd.DataFrame) -> pd.DataFrame:
    """청산 위험 칸을 붙인다. 미국분 판정에는 수집 때 쓴 환율이 필요하다."""
    result = result.copy()
    rate = None
    try:
        import us_etf

        rates = pd.to_numeric(us_etf.load_latest().get("환율"), errors="coerce").dropna()
        rate = float(rates.iloc[0]) if not rates.empty else None
    except Exception:
        pass      # 미국 자료가 없으면 국내만 판정한다

    result["청산위험"] = liquidation_risk(
        result.get("시가총액"), result["상장시장"], rate, result.get("설정일"))
    return result


def attach_ptp(result: pd.DataFrame) -> pd.DataFrame:
    """PTP 여부를 종목코드(티커)로 붙인다. 미국 상장분에만 해당한다."""
    import ptp

    result = result.copy()
    state, expiry = ptp.annotate(result["종목코드"].fillna(""))
    # 국내 상장 ETF 는 6자리 숫자라 목록과 겹칠 일이 없지만, 명시적으로 비워 둔다
    domestic = result["상장시장"] != US_MARKET
    result["PTP"] = state.mask(domestic, "")
    result["PTP면제만료"] = expiry.mask(domestic, "")
    return result


def attach_krx_info(result: pd.DataFrame) -> pd.DataFrame:
    """KRX에서 받아둔 시총·총보수·분류를 ISIN으로 붙인다 (파일 없으면 그대로 둔다)."""
    import etf_info

    info = etf_info.load_latest()
    if result.empty or info.empty:
        return result
    info = info.copy()
    # KRX 정보의 'KRX코드'가 우리 표에서는 '종목코드' 칸이다
    if "종목코드" not in info.columns and "KRX코드" in info.columns:
        info["종목코드"] = info["KRX코드"].astype(str).str.strip()
    # 옛 CSV에는 분류 컬럼이 없을 수 있으니 있는 것만 붙인다
    available = [c for c in KRX_COLUMNS if c in info.columns]
    # errors="ignore": result 에 아직 없는 칸(상장일 등)을 지우려다 죽지 않게
    merged = result.drop(columns=available, errors="ignore").merge(
        info[["ISIN", *available]], on="ISIN", how="left")
    for column in KRX_COLUMNS:
        if column not in merged.columns:
            merged[column] = None

    # 국내 '상장일'은 미국 '설정일'과 같은 뜻이다 → 한 칸에 모은다.
    # 그래야 운용기간·청산위험 판정이 두 시장에서 같은 코드로 돌아간다.
    if "상장일" in merged.columns:
        listed = merged["상장일"].astype(str).str.strip().replace(
            {"nan": "", "None": "", "NaT": ""})
        merged["설정일"] = listed
        merged["운용기간_년"] = listed.map(years_since)

    # 연간분배율(%) = 최근 12개월 분배금 합 ÷ 현재 종가. KRX 종가가 있어야 낼 수 있다
    if "종가" in info.columns:
        price = info[["ISIN", "종가"]].drop_duplicates("ISIN")
        merged = merged.merge(price, on="ISIN", how="left")
        close = pd.to_numeric(merged["종가"], errors="coerce")
        ttm = pd.to_numeric(merged["연간주당분배금"], errors="coerce")
        merged["연간분배율"] = (ttm / close * 100).round(2).where(close > 0)
        # 직전 회차 분배금 ÷ **지금 시장가** — 세이브로 '최근_분배율'은 기준가로
        # 나눈 값이라 지금 가격과 어긋난다. 시장가 기준을 따로 둔다
        recent_amount = pd.to_numeric(merged["최근_주당분배금"], errors="coerce")
        merged["최근분배율_시장가"] = (recent_amount / close * 100).round(2).where(close > 0)
        # 특별분배를 뺀 평상 분배율 — 위와 같은 종가로 나눠야 둘을 견줄 수 있다
        median_ttm = pd.to_numeric(merged["중앙값연주당분배금"], errors="coerce")
        merged["중앙값연분배율"] = (median_ttm / close * 100).round(2).where(close > 0)
        merged = merged.drop(columns="종가")
    return merged[OUTPUT_COLUMNS]


# ── '주기 확인' 플래그의 원인 가르기 ─────────────────────
# 전에는 두 판정의 **순위만 보고** "상장한 지 얼마 안 된 것으로 보임"이라고 적었다.
# 상장일이 이미 수집돼 있는데 추측할 이유가 없다. 실제로 갈라 보니 셋이 섞여 있었다.
NEW_LISTING_MONTHS = 12      # 이 아래면 12개월치가 애초에 안 찬다

CAUSE_NEW = "신규상장"
CAUSE_IRREGULAR = "실제 불규칙"
CAUSE_UNKNOWN = "상장일 없음"


def _months_listed(row, today: pd.Timestamp) -> float | None:
    """상장(설정)한 지 몇 달 됐나. 자료가 없으면 None — 0 으로 두지 않는다."""
    listed = pd.to_datetime(row.get("설정일"), errors="coerce")
    if pd.isna(listed):
        years = pd.to_numeric(pd.Series([row.get("운용기간_년")]),
                              errors="coerce").iloc[0]
        return float(years) * 12 if pd.notna(years) else None
    return (today - listed).days / 30.44


def refine_cycle_flags(result: pd.DataFrame,
                       today: pd.Timestamp | None = None) -> pd.DataFrame:
    """'주기 확인' 종목의 원인을 상장일로 가르고, 신규상장 건은 주기를 바로잡는다.

    **왜 신규상장이면 간격 기준이 맞나**: 상장한 지 6개월인 월배당 ETF 는 12개월
    창에 6회밖에 안 잡혀 '분기'로 분류된다. 횟수는 **적게 나올 수만 있고 많이
    나올 수는 없다.** 그래서 간격 기준이 **더 잦은 쪽**일 때만 그것을 따른다.
    (덜 잦은 쪽이면 횟수가 모자란 탓이 아니므로 손대지 않는다 —
     ACE 유럽방산TOP10 은 3회·간격 106일이라 간격 기준이 오히려 과하다.)

    `분배주기` 값을 실제로 고치므로 캘린더·현금흐름까지 함께 맞아진다.
    """
    if result is None or result.empty or "비고" not in result.columns:
        return result
    today = today or pd.Timestamp.today().normalize()
    out = result.copy()
    flagged = out.index[out["비고"].fillna("") == "주기 확인"]

    for i in flagged:
        row = out.loc[i]
        months = _months_listed(row, today)
        gap = pd.to_numeric(pd.Series([row.get("중앙값간격_일")]),
                            errors="coerce").iloc[0]
        cyc_int = cycle_by_interval(gap) if pd.notna(gap) else None
        cyc_cnt = str(row.get("분배주기") or "")
        detail = str(row.get("비고상세") or "")

        if months is None:
            cause, extra = CAUSE_UNKNOWN, (
                "상장일 자료가 없어 신규상장인지 실제 불규칙인지 가르지 못했습니다.")
        elif months < NEW_LISTING_MONTHS:
            cause = CAUSE_NEW
            extra = (f"상장 {months:.0f}개월째라 12개월치가 아직 안 찼습니다 "
                     "— 횟수가 적게 나온 것이지 주기가 이상한 것이 아닙니다.")
            # 간격 기준이 **더 잦으면** 그쪽이 실제 주기다
            if cyc_int and cyc_cnt in CYCLE_RANK and \
                    CYCLE_RANK[cyc_int] < CYCLE_RANK.get(cyc_cnt, 99):
                out.at[i, "분배주기"] = cyc_int
                extra += f" 지급 간격에 맞춰 **{cyc_cnt} → {cyc_int}** 로 바로잡았습니다."
        else:
            cause = CAUSE_IRREGULAR
            extra = (f"상장 {months / 12:.1f}년째라 신규상장 탓이 아닙니다. "
                     "회차를 거르거나 배당 달이 해마다 달라 간격이 흔들립니다. "
                     "(두 해 연속 같은 3개 달에만 준 종목은 '연3회'로 따로 분류했고, "
                     "여기 남은 것은 그 확인이 안 된 경우입니다.)")
        out.at[i, "비고사유"] = cause
        # 앞 문장(두 판정이 갈렸다는 사실)은 남기고, 뒤의 추측만 갈아 끼운다
        head = detail.split(" 상장한 지")[0].split(" 지급 간격이 들쭉")[0].rstrip(". ")
        out.at[i, "비고상세"] = f"[{cause}] {head}. {extra}" if head else f"[{cause}] {extra}"

    if "비고사유" in out.columns:
        out["비고사유"] = out["비고사유"].fillna("")
    return out


# ── 배수 (레버리지·인버스) ───────────────────────────────
def attach_multiple(result: pd.DataFrame) -> pd.DataFrame:
    """한투에서 받은 **추적수익률 배수**를 종목코드로 붙인다 (kis_multiple.py).

    **이름으로 추정하지 않는다.** 6단계에서 확인했듯 KRX 의 자산분류·운용방식·
    기초지수명으로는 레버리지를 구분할 수 없다(`KODEX 레버리지` 와 `KODEX 200`
    이 전 칸 동일, 기초지수도 똑같이 '코스피 200'). 배수는 한투 API 의
    `etf_trc_ert_mltp` 하나만 근거로 삼는다.

    미국 상장분은 이 API 대상이 아니라 **빈칸**이다 — yfinance 카테고리는
    Leveraged/Inverse 라는 방향만 알려주고 배수(2X·3X)는 안 준다(실측 0건).
    """
    out = result.copy()
    if "배수" not in out.columns:
        out["배수"] = pd.NA
    try:
        import kis_multiple
        table = kis_multiple.load_latest()
    except Exception:
        return out
    if table.empty or "배수" not in table.columns:
        return out
    values = pd.to_numeric(table["배수"], errors="coerce")

    # **ISIN 을 먼저 맞춘다.** 목록의 `종목코드` 는 만기·상폐 종목 234개가 비어
    # 있어서(수집기는 ISIN 가운데 6자리로 채워 넣었다) 종목코드만으로 이으면
    # 그 234개가 통째로 안 붙는다.
    if "ISIN" in table.columns:
        by_isin = dict(zip(table["ISIN"].astype(str).str.strip(), values))
        out["배수"] = out["ISIN"].astype(str).str.strip().map(by_isin)
    if "종목코드" in table.columns:
        by_code = dict(zip(table["종목코드"].astype(str).str.strip(), values))
        filled = out["ISIN"].astype(str).str.strip().map(by_isin) \
            if "ISIN" in table.columns else pd.Series(pd.NA, index=out.index)
        out["배수"] = filled.fillna(
            out["종목코드"].astype(str).str.strip().map(by_code))
    return out


def main():
    src = latest_raw_csv()
    print(f"원본 읽기: {src.name}")
    df = pd.read_csv(src, dtype=str)
    result = classify(df)

    info_csv = None
    distributed = len(result)
    try:
        result = attach_krx_info(result)
        import etf_info
        info_csv = etf_info.latest_info_csv()
        # 분배 이력이 없는 상장 ETF도 후보군으로 함께 담는다
        result = add_undistributed(result, etf_info.load_latest())
    except Exception as exc:   # KRX 정보가 없어도 분류 자체는 끝낼 수 있다
        print(f"※ KRX 시가총액·총보수 결합 실패({exc}) — 해당 칸은 비워 둡니다.")
    print(f"국내 — 분배 이력 있음: {distributed:,}개 / 미분배까지 합계: {len(result):,}개")

    # 미국 직접 상장 ETF (us_etf.py 를 돌려 뒀을 때만)
    try:
        import us_etf
        us = us_etf.load_latest()
        before = len(result)
        result = add_us_etfs(result, us)
        print(f"미국 상장 ETF: {len(result) - before:,}개 추가"
              if len(result) > before else
              "미국 상장 ETF: 없음 (python src/us_etf.py 를 돌리면 채워집니다)")
    except Exception as exc:
        print(f"※ 미국 ETF 결합 실패({exc}) — 국내 종목만 저장합니다.")

    # '주기 확인' 원인 가르기 — 상장일이 붙은 뒤라야 판정할 수 있다
    before_cycles = result["분배주기"].copy()
    result = refine_cycle_flags(result)
    changed = (before_cycles != result["분배주기"]).sum()
    if "비고사유" in result.columns:
        causes = result.loc[result["비고"] == "주기 확인", "비고사유"].value_counts()
        print(f"\n[주기 확인 {int((result['비고'] == '주기 확인').sum())}건 원인]")
        print(causes.to_string() if not causes.empty else "  없음")
        print(f"  신규상장이라 주기를 바로잡은 종목: {changed}개")

    # 청산 위험 — 국내만 있어도 매길 수 있게 합친 뒤에 붙인다
    result = attach_liquidation_risk(result)

    # 배수(레버리지·인버스) — 한투 수집분이 있을 때만 채워진다
    result = attach_multiple(result)
    filled = result["배수"].notna().sum() if "배수" in result.columns else 0
    if filled:
        levered = result[result["배수"].abs() != 1]["배수"].notna().sum()
        print(f"\n배수: {filled:,}종목 채움 (1배 아닌 것 {levered:,}개)")
    else:
        print("\n배수: 수집분 없음 — `python src/kis_multiple.py` 로 받으세요")
    print("KRX 정보: " + (f"{info_csv.name} 사용" if info_csv else
                          "없음 (python src/etf_info.py 를 먼저 실행하면 시총·총보수가 채워집니다)"))

    # 시트2: 월·위클리만 (CLAUDE.md 산출물 정의)
    monthly_weekly = result[result["분배주기"].isin(["월", "위클리"])].reset_index(drop=True)

    out = OUT_DIR / "분배ETF목록.xlsx"
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        result.to_excel(writer, sheet_name="전체", index=False)
        monthly_weekly.to_excel(writer, sheet_name="월·위클리", index=False)
    print(f"저장: {out}")

    # 분배주기별 종목 수 요약
    print("\n[분배주기별 종목 수]")
    print(result["분배주기"].value_counts().reindex(
        ["위클리", "월", "분기", TRI_CYCLE, "반기", "연"]).fillna(0).astype(int).to_string())
    n_flag = (result["비고"] != "").sum()
    print(f"\n전체 종목: {len(result)}개 / 월·위클리: {len(monthly_weekly)}개 "
          f"/ 비고(확인필요): {n_flag}개")
    print("\n[전체 시트 상위 5행]")
    print(result.head().to_string())


if __name__ == "__main__":
    from console import use_utf8_stdout   # 콘솔이 cp949여도 안 죽게
    use_utf8_stdout()
    sys.exit(main())
