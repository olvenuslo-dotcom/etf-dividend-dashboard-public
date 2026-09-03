# -*- coding: utf-8 -*-
"""수급 집계 — 순수 계산. 네트워크를 모른다.

받는 표는 둘이다(둘 다 index 가 날짜):
  trades: 투자자별 **순매수 대금**. 칸 = 개인·외국인·기관합계·금융투자·투신·연기금 …
  shares: 상장주식 수·거래대금 등. 칸 = 상장주식수·거래대금 …

⚠ ETF 의 '금융투자'(증권사) 순매수에는 LP(유동성공급자)의 기계적 헤지 물량이 섞인다.
   '금융투자가 샀다 = 확신 매수' 로 읽으면 안 된다.
"""
import pandas as pd

# 기관합계 칸이 없을 때 대신 더할 세부 항목들 (KRX 표기)
INSTITUTION_PARTS = ("금융투자", "투신", "연기금", "보험", "사모", "은행", "기타금융")
INSTITUTION_TOTAL = "기관합계"
FOREIGN = "외국인"
INDIVIDUAL = "개인"
SHARES = "상장주식수"
TURNOVER = "거래대금"


def _tail(frame: pd.DataFrame, days: int) -> pd.DataFrame:
    """마지막 days 거래일. 상장 기간이 짧으면 있는 만큼만 돌려준다."""
    if frame is None or frame.empty:
        return pd.DataFrame()
    return frame.sort_index().tail(days)


def institution_total(trades: pd.DataFrame) -> pd.Series:
    """기관합계 열. 없으면 세부 항목을 더해서 만든다."""
    if trades is None or trades.empty:
        return pd.Series(dtype="float64")
    if INSTITUTION_TOTAL in trades.columns:
        return pd.to_numeric(trades[INSTITUTION_TOTAL], errors="coerce").fillna(0)
    parts = [c for c in INSTITUTION_PARTS if c in trades.columns]
    if not parts:
        return pd.Series(0.0, index=trades.index)
    return trades[parts].apply(pd.to_numeric, errors="coerce").fillna(0).sum(axis=1)


def net_buy(trades: pd.DataFrame, days: int, who: str) -> float:
    """최근 days 거래일 누적 순매수 대금. who = '기관합계'/'외국인'/'개인'."""
    window = _tail(trades, days)
    if window.empty:
        return 0.0
    if who == INSTITUTION_TOTAL:
        return float(institution_total(window).sum())
    if who not in window.columns:
        return 0.0
    return float(pd.to_numeric(window[who], errors="coerce").fillna(0).sum())


def smart_money(trades: pd.DataFrame, days: int) -> float:
    """기관합계 + 외국인 누적 순매수. 이탈·유입 판정의 기준값이다."""
    return net_buy(trades, days, INSTITUTION_TOTAL) + net_buy(trades, days, FOREIGN)


def shares_change(shares: pd.DataFrame, days: int) -> float:
    """최근 days 거래일 상장주식 수 증감(마지막 - 처음).

    ETF 는 자금이 들어오면 설정으로 주식 수가 늘고, 빠지면 환매로 줄어든다.
    가격과 달리 **자금 흐름을 직접 보여준다.**
    """
    window = _tail(shares, days)
    if window.empty or SHARES not in window.columns:
        return 0.0
    series = pd.to_numeric(window[SHARES], errors="coerce").dropna()
    if len(series) < 2:
        return 0.0
    return float(series.iloc[-1] - series.iloc[0])


def shares_change_pct(shares: pd.DataFrame, days: int) -> float:
    """최근 days 거래일 상장주식수 변화율(%). 표에 보여줄 때는 이쪽이 읽기 쉽다."""
    window = _tail(shares, days)
    if window.empty or SHARES not in window.columns:
        return 0.0
    series = pd.to_numeric(window[SHARES], errors="coerce").dropna()
    if len(series) < 2 or series.iloc[0] <= 0:
        return 0.0
    return float((series.iloc[-1] / series.iloc[0] - 1) * 100)


def shares_rising_streak(shares: pd.DataFrame) -> int:
    """상장주식 수가 마지막 날부터 연속 몇 거래일 늘었나."""
    if shares is None or shares.empty or SHARES not in shares.columns:
        return 0
    series = pd.to_numeric(shares.sort_index()[SHARES], errors="coerce").dropna()
    if len(series) < 2:
        return 0
    streak = 0
    # 뒤에서부터 '전날보다 늘었나'를 세다가 아니면 멈춘다
    for today, yesterday in zip(series.iloc[::-1], series.iloc[-2::-1]):
        if today > yesterday:
            streak += 1
        else:
            break
    return streak


def average_turnover(shares: pd.DataFrame, days: int) -> float:
    """최근 days 거래일 일평균 거래대금."""
    window = _tail(shares, days)
    if window.empty or TURNOVER not in window.columns:
        return 0.0
    series = pd.to_numeric(window[TURNOVER], errors="coerce").dropna()
    return float(series.mean()) if not series.empty else 0.0


def cumulative(trades: pd.DataFrame, days: int, who: str) -> pd.Series:
    """누적 순매수 추이(차트용). 기간 시작을 0 으로 놓고 쌓아 간다."""
    window = _tail(trades, days)
    if window.empty:
        return pd.Series(dtype="float64")
    if who == INSTITUTION_TOTAL:
        daily = institution_total(window)
    elif who in window.columns:
        daily = pd.to_numeric(window[who], errors="coerce").fillna(0)
    else:
        return pd.Series(dtype="float64")
    return daily.cumsum()


def summarize(trades: pd.DataFrame, shares: pd.DataFrame,
              period_days: int) -> dict:
    """관심종목 요약 표 한 줄에 들어갈 값들.

    20일·60일을 **둘 다** 낸다. 단기 출렁임만 보고 오경보를 내지 않기 위해서다.
    """
    return {
        "20일_기관": net_buy(trades, 20, INSTITUTION_TOTAL),
        "20일_외국인": net_buy(trades, 20, FOREIGN),
        "20일_개인": net_buy(trades, 20, INDIVIDUAL),
        "60일_기관": net_buy(trades, 60, INSTITUTION_TOTAL),
        "60일_외국인": net_buy(trades, 60, FOREIGN),
        "60일_개인": net_buy(trades, 60, INDIVIDUAL),
        "20일_스마트머니": smart_money(trades, 20),
        "60일_스마트머니": smart_money(trades, 60),
        "상장주식수_증감": shares_change(shares, period_days),
        "상장주식수_증감_20일": shares_change(shares, 20),
        "상장주식수_증감_60일": shares_change(shares, 60),
        "상장주식수_증감률_20일": shares_change_pct(shares, 20),
        "상장주식수_증감률_60일": shares_change_pct(shares, 60),
        "연속증가일": shares_rising_streak(shares),
        "일평균거래대금": average_turnover(shares, period_days),
        # 상장 기간이 조회 기간보다 짧을 수 있다 → 화면에 'N일치'로 알린다
        "데이터일수": int(len(shares)) if shares is not None else 0,
    }
