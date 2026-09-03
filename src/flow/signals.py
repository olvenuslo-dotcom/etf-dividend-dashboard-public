# -*- coding: utf-8 -*-
"""경고 플래그 판정 — 순수 계산. 네트워크를 모른다.

PRD **v1.1** (docs/수급_PRD.md) 의 규칙이다. 숫자 기준은 초기값이고, 바꾸면 PRD 도 고친다.

v1.0 은 투자자별 순매수(기관·외국인)를 썼는데, 그 데이터가 합법 자동화 경로에
없어서(KRX Open API 미제공·pykrx 차단·네이버 robots 차단) **상장주식수(설정·환매)**
기반으로 재설계했다. ETF 는 자금이 들어오면 설정으로 주식수가 늘고 빠지면 줄어서,
이쪽이 오히려 LP 헤지가 섞이지 않는 깨끗한 신호다.

  🔴 이탈  ① 최근 20일 상장주식수 감소
           ② 최근 60일 상장주식수 감소      ← 20일만 보면 오경보가 난다
  🟢 유입  ① 최근 5거래일 연속 상장주식수 증가
           ② 최근 20일 상장주식수 증가
  ⚠ 함정   ① 연환산 분배율이 비교 대상 중 상위 30%
           ② 🔴 이탈이 켜져 있음
"""
import pandas as pd

OUTFLOW = "🔴 이탈"
INFLOW = "🟢 유입"
TRAP = "⚠️ 분배율 함정"
NO_YIELD = "분배율 미연결"

RISING_DAYS_FOR_INFLOW = 5      # 🟢 ①
TOP_YIELD_QUANTILE = 0.70       # ⚠ ① 상위 30% = 70 분위 이상


def outflow(summary: dict) -> bool:
    """🔴 이탈 경고 — 환매 우위가 단기(20일)·중기(60일) 모두 이어질 때."""
    return (summary.get("상장주식수_증감_20일", 0) < 0
            and summary.get("상장주식수_증감_60일", 0) < 0)


def inflow(summary: dict) -> bool:
    """🟢 유입 신호 — 설정이 연속으로 이어지고 20일 합계도 늘었을 때."""
    return (summary.get("연속증가일", 0) >= RISING_DAYS_FOR_INFLOW
            and summary.get("상장주식수_증감_20일", 0) > 0)


def flags_for(summary: dict) -> list[str]:
    """한 종목의 플래그 목록 (분배율 없이도 계산된다)."""
    out = []
    if outflow(summary):
        out.append(OUTFLOW)
    if inflow(summary):
        out.append(INFLOW)
    return out


def flag_distribution_trap(분배율표: pd.DataFrame) -> pd.DataFrame:
    """⚠ 분배율 함정 판정. **기존 분배 툴과의 연결 지점은 이 함수 하나다.**

    입력: (종목코드, 연환산 분배율, 이탈) 세 열을 가진 표
      · 종목코드 — 문자열
      · 연환산 분배율 — 숫자(%)
      · 이탈 — bool. flags_for() 로 이미 판정해 둔 🔴 여부
    출력: 입력에 '함정' (bool) 열이 붙은 표

    분배율 열이 비어 있으면 판정하지 않고 함정=False 로 둔다
    (화면에서는 '분배율 미연결' 로 보여준다).
    """
    frame = 분배율표.copy()
    yields = pd.to_numeric(frame.get("연환산 분배율"), errors="coerce")

    if yields is None or yields.dropna().empty:
        frame["함정"] = False
        return frame

    # 상위 30% 경계값. 비교 대상이 하나뿐이면 분위수가 자기 자신이라 의미가 없다
    if yields.dropna().nunique() < 2:
        frame["함정"] = False
        return frame

    threshold = yields.quantile(TOP_YIELD_QUANTILE)
    high_yield = yields >= threshold
    outflowing = frame.get("이탈", pd.Series(False, index=frame.index)).fillna(False)
    frame["함정"] = (high_yield & outflowing.astype(bool)).fillna(False)
    return frame
