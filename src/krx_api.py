# -*- coding: utf-8 -*-
"""KRX Open API — 공식 경로 (openapi.krx.co.kr 에서 인증키 발급).

pykrx 가 쓰던 data.krx.co.kr 스크래핑은 약관 위반으로 차단됐다(PROGRESS 20단계).
여기는 KRX 가 지정한 공식 API 만 쓴다. 인증키는 `.env` 의 `KRX_OPENAPI_KEY`.

지금 승인된 서비스: 유가증권 일별매매(stk_bydd_trd) · KOSPI 지수.
ETF·코스닥·종목기본정보는 승인 대기 — 나면 여기에 함수를 더한다.

첫 용도: **월말 KOSPI 시총**을 모아 예탁금/시총 비중의 분모를 만든다.
받은 달은 data/krx/코스피시총_월별.csv 에 쌓여서 다시 부르지 않는다.

실행: venv\\Scripts\\python src\\krx_api.py        (기본 36개월)
      venv\\Scripts\\python src\\krx_api.py 60     (60개월치까지 채움)
"""
from pathlib import Path
import os
import sys
import time

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

BASE = Path(__file__).resolve().parent.parent
CAPS_CSV = BASE / "data" / "krx" / "코스피시총_월별.csv"
KOSDAQ_CAPS_CSV = BASE / "data" / "krx" / "코스닥시총_월별.csv"

API = "http://data-dbg.krx.co.kr/svc/apis"
PAUSE = 0.35        # 호출 사이 간격(초) — 몰아치지 않는다

# 시장별 일별매매 경로. 시총 수집이 두 시장을 같은 코드로 돌게 한다.
MARKET_PATHS = {"KOSPI": "/sto/stk_bydd_trd", "KOSDAQ": "/sto/ksq_bydd_trd"}


def _key() -> str:
    import env_keys

    env_keys.load()
    key = os.environ.get("KRX_OPENAPI_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "KRX_OPENAPI_KEY 가 없습니다. https://openapi.krx.co.kr/ 에서 발급받아 "
            ".env 에 넣어 주세요.")
    return key


def _fetch_rows(path: str, bas_dd: str) -> list[dict]:
    """하루치 원본 행들. 응답이 비면 빈 목록(휴장이거나 아직 미배포)."""
    import requests

    r = requests.get(API + path, headers={"AUTH_KEY": _key()},
                     params={"basDd": bas_dd}, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"KRX {r.status_code}: {r.text[:80]}")
    return r.json().get("OutBlock_1", [])


def fetch_market_cap(bas_dd: str, market: str = "KOSPI") -> float | None:
    """하루치 전 종목 시총 합(조원). 응답이 비면 None."""
    rows = _fetch_rows(MARKET_PATHS[market], bas_dd)
    if not rows:
        return None
    total = sum(float(x.get("MKTCAP") or 0) for x in rows)
    return round(total / 1e12, 1)      # 원 → 조원


def fetch_kospi_cap(bas_dd: str) -> float | None:
    """collect() 의 기본 fetcher (예전 이름 유지)."""
    return fetch_market_cap(bas_dd, "KOSPI")


def fetch_etf_daily(bas_dd: str) -> pd.DataFrame:
    """하루치 국내 ETF 전 종목 — 시세·NAV·순자산·상장주식수·기초지수명.

    pykrx 가 차단된 뒤 국내 ETF 갱신은 이 경로가 유일한 공식 통로다.
    LIST_SHRS(상장주식수)는 나중에 수급(설정·환매) 추적에도 쓴다.
    """
    rows = _fetch_rows("/etp/etf_bydd_trd", bas_dd)
    if not rows:
        return pd.DataFrame()

    def num(x, key):
        return pd.to_numeric(str(x.get(key) or "").replace(",", ""), errors="coerce")

    return pd.DataFrame({
        "KRX코드": [str(x.get("ISU_CD") or "").strip() for x in rows],
        "종목명": [str(x.get("ISU_NM") or "").strip() for x in rows],
        "종가": [num(x, "TDD_CLSPRC") for x in rows],
        "NAV": [num(x, "NAV") for x in rows],
        "시가총액": [num(x, "MKTCAP") for x in rows],
        "순자산총액": [num(x, "INVSTASST_NETASST_TOTAMT") for x in rows],
        "상장주식수": [num(x, "LIST_SHRS") for x in rows],
        "거래대금": [num(x, "ACC_TRDVAL") for x in rows],
        "기초지수명": [str(x.get("IDX_IND_NM") or "").strip() for x in rows],
        "기준일자": bas_dd,
    })


def fetch_etf_daily_with_fallback(bas_dd: str, tries: int = 5) -> pd.DataFrame:
    """그날 자료가 아직 없으면(장 마감 직후 등) 직전 개장일로 물러난다."""
    import exchange_calendars as xcals

    krx = xcals.get_calendar(
        "XKRX",
        start=(pd.Timestamp(bas_dd) - pd.Timedelta(days=30)).strftime("%Y-%m-%d"),
        end=pd.Timestamp(bas_dd).strftime("%Y-%m-%d"))
    stamp = pd.Timestamp(bas_dd)
    for _ in range(tries):
        frame = fetch_etf_daily(stamp.strftime("%Y%m%d"))
        if not frame.empty:
            return frame
        stamp = krx.date_to_session(stamp - pd.Timedelta(days=1),
                                    direction="previous")
        time.sleep(PAUSE)
    return pd.DataFrame()


def month_end_sessions(months_back: int) -> list[str]:
    """최근 months_back 개월의 **월별 마지막 개장일** (YYYYMMDD, 과거→최근).

    이번 달은 '오늘까지 중 마지막 개장일'이 잡힌다 — 월이 끝나면 값이 바뀔 수
    있어서, collect() 가 이번 달만은 다시 받아 갱신한다.
    """
    import exchange_calendars as xcals

    today = pd.Timestamp.today().normalize()
    start = (today - pd.DateOffset(months=months_back)).replace(day=1)
    krx = xcals.get_calendar("XKRX", start=start.strftime("%Y-%m-%d"),
                             end=today.strftime("%Y-%m-%d"))
    sessions = pd.Series(krx.sessions)
    last_per_month = sessions.groupby(sessions.dt.to_period("M")).max()
    return [s.strftime("%Y%m%d") for s in last_per_month]


def _caps_path(market: str) -> Path:
    """시장별 시총 CSV 경로 — 모듈 속성을 거쳐야 테스트가 바꿔칠 수 있다."""
    return CAPS_CSV if market == "KOSPI" else KOSDAQ_CAPS_CSV


def load(market: str = "KOSPI") -> pd.DataFrame:
    """쌓아 둔 월별 시총 (월=YYYYMM 문자열, 시총_조원)."""
    path = _caps_path(market)
    if not path.exists():
        return pd.DataFrame(columns=["월", "기준일", "시총_조원"])
    frame = pd.read_csv(path, dtype={"월": str, "기준일": str})
    frame["시총_조원"] = pd.to_numeric(frame["시총_조원"], errors="coerce")
    return frame.dropna(subset=["시총_조원"])


def collect(months: int = 36, fetcher=None, market: str = "KOSPI") -> pd.DataFrame:
    """부족한 달만 API 로 받아 CSV 에 더한다. fetcher 는 테스트용 주입 자리."""
    injected = fetcher is not None      # 테스트 주입이면 API 간격을 안 둔다
    fetcher = fetcher or (lambda d: fetch_market_cap(d, market))
    history = load(market)
    done = set(history["월"])
    current_month = pd.Timestamp.today().strftime("%Y%m")

    rows = []
    for bas_dd in month_end_sessions(months):
        month = bas_dd[:6]
        # 지나간 달은 다시 안 받는다. 이번 달만 갱신한다(월말 전이라 값이 바뀐다).
        if month in done and month != current_month:
            continue
        try:
            cap = fetcher(bas_dd)
        except Exception as exc:
            print(f"  {bas_dd}: 실패 ({type(exc).__name__}: {str(exc)[:60]})")
            continue
        if cap is None:
            print(f"  {bas_dd}: 응답 없음(휴장?)")
            continue
        rows.append({"월": month, "기준일": bas_dd, "시총_조원": cap})
        print(f"  {month}: {cap:,.0f}조원 ({bas_dd})", flush=True)
        if not injected:
            time.sleep(PAUSE)

    if rows:
        merged = (pd.concat([history, pd.DataFrame(rows)], ignore_index=True)
                  if not history.empty else pd.DataFrame(rows))
        merged = (merged.drop_duplicates("월", keep="last")
                  .sort_values("월").reset_index(drop=True))
        path = _caps_path(market)
        path.parent.mkdir(parents=True, exist_ok=True)
        merged.to_csv(path, index=False, encoding="utf-8-sig")
        return merged
    return history


def main() -> int:
    months = int(sys.argv[1]) if len(sys.argv) > 1 else 36
    for market in ("KOSPI", "KOSDAQ"):
        print(f"{market} 월말 시총 수집 — 최근 {months}개월 (이미 받은 달은 건너뜀)")
        out = collect(months, market=market)
        if out.empty:
            print("  받은 게 없습니다.")
            continue
        print(f"  이력 {len(out)}개월 ({out['월'].min()}~{out['월'].max()}) "
              f"→ {_caps_path(market)}")
    return 0


if __name__ == "__main__":
    from console import use_utf8_stdout   # 콘솔이 cp949여도 안 죽게
    use_utf8_stdout()
    from corp_ca import apply as apply_corp_ca   # 회사 HTTPS 가로채기 뒤에서도 받게
    apply_corp_ca()
    sys.exit(main())
