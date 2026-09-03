# -*- coding: utf-8 -*-
"""PTP(Publicly Traded Partnership) 종목 판정.

미국 세법 §1446(f) 에 따라, PTP 로 분류된 종목을 팔면 **매도 대금의 10%** 가
원천징수된다. 배당이 아니라 **판 금액 전체**가 기준이라, 손실을 보고 팔아도 떼인다.
연 1%도 안 되는 총보수를 비교하다가 이걸 놓치면 계산이 통째로 틀어진다.

발행사가 면제(exemption) 를 신청하면 일정 기간 빠지는데, **면제에는 만료일이 있다.**
오늘은 면제여도 다음 달에 다시 대상이 될 수 있어, 만료일을 같이 보여 준다.

자료: 증권사가 공지하는 PTP 목록 → data/reference/ptp_목록.csv
（목록은 수시로 바뀐다. 매매 전 거래 증권사 공지로 최종 확인할 것.）
"""
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parent.parent
PTP_CSV = BASE / "data" / "reference" / "ptp_목록.csv"

# 판정 결과
NOT_PTP = ""                 # 목록에 없음
SUBJECT = "대상"             # 매도대금 10% 원천징수
EXEMPT = "면제"              # 지금은 빠져 있다 (만료일 있음)


def load() -> pd.DataFrame:
    """PTP 목록. 파일이 없으면 빈 표(대시보드는 이 칸만 비운 채 그대로 돈다)."""
    if not PTP_CSV.exists():
        return pd.DataFrame(columns=["거래소", "코드", "종목명", "면제",
                                     "면제시작일", "면제종료일"])
    frame = pd.read_csv(PTP_CSV, dtype=str).fillna("")
    frame["코드"] = frame["코드"].str.strip().str.upper()
    return frame


def _status_of_row(row, today: pd.Timestamp) -> str:
    """한 줄의 오늘 기준 상태."""
    if str(row.get("면제") or "").strip() != "해당":
        return SUBJECT
    start = pd.to_datetime(row.get("면제시작일"), errors="coerce")
    end = pd.to_datetime(row.get("면제종료일"), errors="coerce")
    # 기간을 못 읽으면 면제로 단정하지 않는다 (안전한 쪽으로)
    if pd.isna(start) or pd.isna(end):
        return SUBJECT
    return EXEMPT if start <= today <= end else SUBJECT


def status_map(today=None) -> dict[str, tuple[str, str]]:
    """{티커: (상태, 면제만료일)}.

    같은 코드가 여러 거래소에 있으면 **대상 쪽을 우선**한다. 놓쳐서 10% 떼이는 쪽이
    괜히 경고를 보는 쪽보다 손해가 크기 때문이다.
    """
    today = pd.Timestamp(today) if today is not None else pd.Timestamp.today().normalize()
    frame = load()
    out: dict[str, tuple[str, str]] = {}
    for _, row in frame.iterrows():
        code = str(row["코드"]).strip().upper()
        if not code:
            continue
        state = _status_of_row(row, today)
        expiry = str(row.get("면제종료일") or "").strip() if state == EXEMPT else ""
        # 이미 '대상'으로 잡힌 코드는 면제로 덮어쓰지 않는다
        if code in out and out[code][0] == SUBJECT:
            continue
        out[code] = (state, expiry)
    return out


def annotate(codes: pd.Series, today=None) -> tuple[pd.Series, pd.Series]:
    """종목코드 열을 받아 (PTP상태, 면제만료일) 두 열을 돌려준다."""
    table = status_map(today)
    keys = codes.astype(str).str.strip().str.upper()
    state = keys.map(lambda c: table.get(c, (NOT_PTP, ""))[0])
    expiry = keys.map(lambda c: table.get(c, (NOT_PTP, ""))[1])
    return state, expiry
