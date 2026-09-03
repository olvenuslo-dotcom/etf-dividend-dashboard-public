# -*- coding: utf-8 -*-
"""
2단계: 매수마감일/배당락일 캘린더(.ics) 생성
- 입력: output/분배ETF목록.xlsx (1단계 산출물, '전체' 시트)  ※ 청산분배는 1단계에서 이미 제외됨
- 범위: 전체 분배주기 종목, 오늘부터 향후 6개월 안에 오는 회차(예측)
- 영업일: exchange_calendars 의 XKRX(한국거래소 개장일) 사용
  * 근로자의날(5/1)·연말 폐장일·대체공휴일까지 정확히 반영 (holidays 패키지는 이걸 놓침)
- 계산: 배당락일 = 지급기준일 1영업일 전 / 매수마감일 = 지급기준일 2영업일 전
- 미래 지급기준일은 '최근 기준일 + 분배주기'로 추정한 값 → 이벤트에 (예상) 명시
산출물: output/분배캘린더.ics (구글/아웃룩 구독용)
"""
from pathlib import Path
from datetime import date, datetime, timezone
import sys
import pandas as pd
import exchange_calendars as xcals

BASE = Path(__file__).resolve().parent.parent
XLSX = BASE / "output" / "분배ETF목록.xlsx"
ICS_OUT = BASE / "output" / "분배캘린더.ics"

HORIZON_MONTHS = 6      # 향후 몇 개월치까지 예측할지

# 분배주기 → 다음 회차까지 더할 개월 수(월 단위) 또는 주 단위
CYCLE_MONTHS = {"월": 1, "분기": 3, "반기": 6, "연": 12}
CYCLE_WEEKS = {"위클리": 1}
# 연3회(예: 4·7·10월)는 간격이 3·3·6개월로 일정하지 않아 개월 수 하나로 못 민다
# → 분배ETF목록의 '지급월목록' 달로 예측한다
TRI_CYCLE = "연3회"


def parse_pay_months(text) -> list[int] | None:
    """'4,7,10' → [4, 7, 10]. 비거나 이상하면 None (틀린 달로 예측하지 않는다)."""
    try:
        months = sorted({int(p) for p in str(text).split(",")})
    except ValueError:
        return None
    return months if months and all(1 <= m <= 12 for m in months) else None


def _keep_day(anchor: pd.Timestamp, year: int, month: int) -> pd.Timestamp:
    """anchor 의 '일(day)' 성격을 유지해 해당 연·월의 날짜를 만든다.
    월말 지급 종목은 월말로, 그 외는 같은 일(말일 넘으면 말일)."""
    month_end = pd.Timestamp(year, month, 1) + pd.offsets.MonthEnd(0)
    is_month_end = (anchor + pd.Timedelta(days=1)).month != anchor.month
    if is_month_end:
        return month_end
    return pd.Timestamp(year, month, min(anchor.day, month_end.day))


def add_cycle(anchor: pd.Timestamp, cyc: str, k: int,
              pay_months: list[int] | None = None) -> pd.Timestamp:
    """기준일(anchor)에서 k회차 뒤의 예상 지급기준일.
    월말 지급 종목은 월말을 유지(예: 6/30 → 7/31)."""
    if cyc in CYCLE_WEEKS:
        return anchor + pd.Timedelta(weeks=CYCLE_WEEKS[cyc] * k)
    if cyc == TRI_CYCLE:
        if not pay_months:
            return None   # 달 목록 없이는 예측하지 않는다 (틀린 달을 만들지 않게)
        # anchor 다음에 오는 지급월부터 k번째 — 해가 넘어가면 이어서 센다
        later = [m for m in pay_months if m > anchor.month]
        year, idx = (anchor.year, pay_months.index(later[0])) if later \
            else (anchor.year + 1, 0)
        idx += k - 1
        year += idx // len(pay_months)
        return _keep_day(anchor, year, pay_months[idx % len(pay_months)])
    months = CYCLE_MONTHS.get(cyc)
    if months is None:
        return None
    is_month_end = (anchor + pd.Timedelta(days=1)).month != anchor.month
    if is_month_end:
        # 월말 → 대상 월의 월말로
        return anchor + pd.offsets.MonthEnd(months * k)
    # 그 외 → 같은 '일(day)' 유지(말일 넘으면 자동 보정)
    return anchor + pd.DateOffset(months=months * k)


def ymd(d) -> str:
    """날짜를 짧게: 2026-08-14 → 26-8-14"""
    return f"{d.year % 100:02d}-{d.month}-{d.day}"


def esc(text: str) -> str:
    """ICS 텍스트 이스케이프 (역슬래시, 세미콜론, 콤마, 줄바꿈)."""
    s = str(text)
    s = s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
    s = s.replace("\r\n", "\\n").replace("\n", "\\n")
    return s


def fold(line: str) -> str:
    """RFC 5545 줄 접기: 75옥텟 넘으면 접되, UTF-8 글자 중간을 안 자르게 바이트 단위 처리."""
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line
    out, cur, first = [], b"", True
    for ch in line:
        b = ch.encode("utf-8")
        limit = 75 if first else 74  # 이어지는 줄은 맨 앞 공백 1칸 포함
        if len(cur) + len(b) > limit:
            out.append((b"" if first else b" ") + cur)
            cur, first = b"", False
        cur += b
    out.append((b"" if first else b" ") + cur)
    return "\r\n".join(p.decode("utf-8") for p in out)


def vevent(uid, dt: date, summary, description) -> list[str]:
    """하루짜리(all-day) VEVENT 블록 생성."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    d = dt.strftime("%Y%m%d")
    d_end = (dt + pd.Timedelta(days=1)).strftime("%Y%m%d")
    return [
        "BEGIN:VEVENT",
        fold(f"UID:{uid}"),
        f"DTSTAMP:{stamp}",
        f"DTSTART;VALUE=DATE:{d}",
        f"DTEND;VALUE=DATE:{d_end}",
        fold(f"SUMMARY:{esc(summary)}"),
        fold(f"DESCRIPTION:{esc(description)}"),
        "TRANSP:TRANSPARENT",
        "END:VEVENT",
    ]


def market_helpers(calendar):
    """개장일 스냅·n영업일 전 함수를 한 거래소 캘린더로 만들어 준다."""

    def snap_session(ts: pd.Timestamp) -> pd.Timestamp:
        """ts가 개장일이면 그대로, 아니면 직전 개장일로 스냅."""
        ts = pd.Timestamp(ts.date())
        return (ts if calendar.is_session(ts)
                else calendar.date_to_session(ts, direction="previous"))

    def prev_trading(ts: pd.Timestamp, n: int) -> pd.Timestamp:
        """ts(개장일로 스냅)에서 n 영업일 전 개장일."""
        cur = snap_session(ts)
        for _ in range(n):
            cur = calendar.previous_session(cur)
        return cur

    return snap_session, prev_trading


def build():
    today = pd.Timestamp(date.today())
    horizon = today + pd.DateOffset(months=HORIZON_MONTHS)
    cal_start = (today - pd.DateOffset(months=2)).strftime("%Y-%m-%d")
    cal_end = (horizon + pd.DateOffset(months=1)).strftime("%Y-%m-%d")
    # 한국(XKRX)·미국(XNYS) 두 거래소 — 종목이 상장된 시장 쪽 개장일로 계산한다
    helpers = {
        "한국": market_helpers(xcals.get_calendar("XKRX", start=cal_start, end=cal_end)),
        "미국": market_helpers(xcals.get_calendar("XNYS", start=cal_start, end=cal_end)),
    }

    df = pd.read_excel(XLSX, sheet_name="전체")
    df["최근_지급기준일"] = pd.to_datetime(df["최근_지급기준일"], errors="coerce")
    df["최근_실지급일"] = pd.to_datetime(df["최근_실지급일"], errors="coerce")
    df["비고"] = df["비고"].fillna("")   # 비고 없는 종목은 빈 문자열로

    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0",
        "PRODID:-//etf-dividend//분배캘린더//KR",
        "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
        "X-WR-CALNAME:분배ETF 매수마감·배당락",
        "X-WR-TIMEZONE:Asia/Seoul",
    ]

    n_events, n_stocks, samples = 0, 0, []
    for _, r in df.iterrows():
        anchor = r["최근_지급기준일"]
        cyc = r["분배주기"]
        if pd.isna(anchor) or cyc not in {**CYCLE_MONTHS, **CYCLE_WEEKS, TRI_CYCLE: 0}:
            continue
        pay_months = parse_pay_months(r.get("지급월목록")) if cyc == TRI_CYCLE else None
        if cyc == TRI_CYCLE and not pay_months:
            continue   # 달 목록이 없으면 틀린 달로 예측하느니 이벤트를 안 만든다
        market = str(r.get("상장시장") or "한국").strip()
        snap_session, prev_trading = helpers.get(market, helpers["한국"])
        # 미국은 야후가 주는 날짜가 이미 **배당락일**이라 그날이 곧 배당락(T-0),
        # 매수마감은 그 1영업일 전. 국내는 지급기준일 기준이라 T-1 / T-2 다.
        exdiv_back, buyclose_back = (0, 1) if market == "미국" else (1, 2)
        # 예상 지급일 간격(달력일): 최근 실지급일 - 최근 기준일
        lag = (r["최근_실지급일"] - anchor).days if pd.notna(r["최근_실지급일"]) else None
        rate = r.get("최근_분배율")   # 세이브로가 준 직전 회차 분배율(%)

        used = False
        for k in range(1, 400):
            p = add_cycle(anchor, cyc, k, pay_months)
            if p is None or p > horizon:
                break
            if p <= today:
                continue
            rec = snap_session(p)                   # 예상 지급기준일(개장일로 스냅)
            exdiv = prev_trading(rec, exdiv_back).date()      # 배당락
            buyclose = prev_trading(rec, buyclose_back).date()  # 매수마감
            pay = (rec + pd.Timedelta(days=lag)).date() if lag is not None else None

            # 설명: 운용사는 빼고, 주기·직전 분배율·예상일만 짧게
            desc = f"주기: {cyc}"
            if pd.notna(rate):
                desc += f" | 직전 분배율: {rate:.2f}%"
            desc += f" | 예상 기준일: {ymd(rec)}"
            if pay:
                desc += f" | 예상 지급일: {ymd(pay)}"
            desc += " | ※ 예상치(실제는 공시로 확정)"
            if str(r["비고"]).strip():
                desc += f" | 참고: {r['비고']}"

            # 미국 종목은 ISIN 이 비어 있어서 종목명을 식별자로 쓴다
            key = str(r["ISIN"] or "").strip() or f"US-{r['종목명']}"
            base_uid = f"{key}-{rec.strftime('%Y%m%d')}"
            # 매수마감 이벤트
            if buyclose >= today.date():
                lines += vevent(f"{base_uid}-buyclose@etf-div", buyclose,
                                f"[매수마감] {r['종목명']}", desc)
                n_events += 1
            # 배당락 이벤트
            if exdiv >= today.date():
                lines += vevent(f"{base_uid}-exdiv@etf-div", exdiv,
                                f"[배당락] {r['종목명']}", desc)
                n_events += 1
            used = True
            if len(samples) < 3 and cyc == "월":
                samples.append((r["종목명"], cyc, rec.date(), exdiv, buyclose))
        if used:
            n_stocks += 1

    lines.append("END:VCALENDAR")
    # newline="" : 윈도우에서 \n 이 \r\n 으로 재변환돼 \r\r\n 이 되는 것을 방지
    ICS_OUT.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8", newline="")

    print(f"저장: {ICS_OUT}")
    print(f"오늘: {today.date()} ~ 향후 {HORIZON_MONTHS}개월({horizon.date()})")
    print(f"이벤트 종목: {n_stocks}개 / 총 이벤트: {n_events}개")
    print("\n[계산 검증용 샘플 (월배당, 예상 첫 회차)]")
    print(f"{'종목명':22s} {'주기':4s} {'예상지급기준일':12s} {'배당락(T-1)':12s} {'매수마감(T-2)':12s}")
    for nm, c, p, e, b in samples:
        print(f"{nm:22s} {c:4s} {str(p):12s} {str(e):12s} {str(b):12s}")


if __name__ == "__main__":
    from console import use_utf8_stdout   # 콘솔이 cp949여도 안 죽게
    use_utf8_stdout()
    from corp_ca import apply as apply_corp_ca   # 회사 HTTPS 가로채기 뒤에서도 받게
    apply_corp_ca()
    sys.exit(build())
