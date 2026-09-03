# -*- coding: utf-8 -*-
"""수급 데이터 조회 + 캐시 — **외부 세계와 닿는 유일한 파일** (PRD 8절).

KRX Open API ETF 일별매매는 **하루 호출 한 번에 전 종목**이 온다. 그래서 캐시도
종목이 아니라 **날 단위**다: data/flow_cache/etf_YYYYMMDD.csv (전 ETF 의
상장주식수·거래대금·종가·NAV). 관심종목이 늘어도 다시 받을 게 없다.

캐시 규칙(PRD 6.3, 바꾸지 말 것):
  · 받아 둔 날은 다시 요청하지 않는다
  · 응답이 빈 날(휴장·미배포)은 파일을 만들지 않는다 → 다음 실행 때 다시 시도
  · 화면 렌더 중에는 API 를 부르지 않는다 — 버튼 또는 CLI 로만 받는다

실행: venv\\Scripts\\python src\\flow\\fetch.py 60     (최근 60거래일 채움)
"""
from pathlib import Path
import sys

import pandas as pd

BASE = Path(__file__).resolve().parent.parent.parent
CACHE_DIR = BASE / "data" / "flow_cache"
WATCHLIST_CSV = BASE / "data" / "reference" / "관심종목.csv"

# 캐시에 남기는 칸 — 수급 판정과 상세 차트에 필요한 것만
CACHE_COLUMNS = ["KRX코드", "종목명", "상장주식수", "거래대금", "종가", "NAV"]

sys.path.insert(0, str(BASE / "src"))


# ── 관심종목 ──────────────────────────────────────────────
def load_watchlist() -> pd.DataFrame:
    """(종목코드, 종목명). 파일이 없으면 빈 표."""
    if not WATCHLIST_CSV.exists():
        return pd.DataFrame(columns=["종목코드", "종목명"])
    frame = pd.read_csv(WATCHLIST_CSV, dtype=str).fillna("")
    frame["종목코드"] = frame["종목코드"].str.strip()
    return frame[frame["종목코드"] != ""].reset_index(drop=True)


def save_watchlist(frame: pd.DataFrame) -> None:
    """관심종목 저장 — **다른 칸을 지우지 않는다.**

    같은 파일을 관심 플랜 탭(watch.py)이 수량·투자금액·메모 칸으로 같이 쓴다.
    여기서 (종목코드, 종목명)만 저장하면 그 칸들이 날아가므로,
    기존 파일의 나머지 칸을 종목코드 기준으로 붙여 보존한다.
    """
    WATCHLIST_CSV.parent.mkdir(parents=True, exist_ok=True)
    out = frame[["종목코드", "종목명"]].drop_duplicates("종목코드").copy()
    if WATCHLIST_CSV.exists():
        old = pd.read_csv(WATCHLIST_CSV, dtype={"종목코드": str}).fillna("")
        extras = [c for c in old.columns if c not in ("종목코드", "종목명")]
        if extras:
            old["종목코드"] = old["종목코드"].astype(str).str.strip()
            out = out.merge(old[["종목코드", *extras]], on="종목코드", how="left")
    out.to_csv(WATCHLIST_CSV, index=False, encoding="utf-8-sig")


# ── 날 단위 캐시 ──────────────────────────────────────────
def _day_path(bas_dd: str) -> Path:
    return CACHE_DIR / f"etf_{bas_dd}.csv"


def sessions_back(days: int) -> list[str]:
    """오늘까지의 최근 days 개장일 (YYYYMMDD, 과거→최근)."""
    import exchange_calendars as xcals

    today = pd.Timestamp.today().normalize()
    start = today - pd.Timedelta(days=int(days * 1.7) + 10)   # 주말·휴일 여유
    krx = xcals.get_calendar("XKRX", start=start.strftime("%Y-%m-%d"),
                             end=today.strftime("%Y-%m-%d"))
    return [s.strftime("%Y%m%d") for s in krx.sessions[-days:]]


def cached_days() -> list[str]:
    """캐시에 있는 날들 (YYYYMMDD, 정렬)."""
    if not CACHE_DIR.exists():
        return []
    return sorted(p.stem.replace("etf_", "") for p in CACHE_DIR.glob("etf_*.csv"))


def ensure_days(days: int, fetcher=None, progress=None) -> int:
    """최근 days 개장일 중 캐시에 없는 날만 받아 저장한다. 받은 날 수를 돌려준다.

    fetcher: 테스트용 주입 자리 (기본은 krx_api.fetch_etf_daily).
    progress: 진행 콜백 (화면 스피너용) — progress(몇번째, 전체, 날짜).
    """
    if fetcher is None:
        import krx_api
        fetcher = krx_api.fetch_etf_daily

    todo = [d for d in sessions_back(days) if not _day_path(d).exists()]
    fetched = 0
    for i, bas_dd in enumerate(todo, start=1):
        if progress:
            progress(i, len(todo), bas_dd)
        try:
            frame = fetcher(bas_dd)
        except Exception as exc:
            # 앱을 죽이지 않는다(PRD 9) — 이 날만 건너뛰고 다음 실행 때 다시 온다
            print(f"  {bas_dd}: 실패 ({type(exc).__name__}: {str(exc)[:60]})")
            continue
        if frame is None or frame.empty:
            continue                      # 휴장·미배포 — 파일을 안 만든다
        keep = frame[[c for c in CACHE_COLUMNS if c in frame.columns]]
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        keep.to_csv(_day_path(bas_dd), index=False, encoding="utf-8-sig")
        fetched += 1
    return fetched


def shares_history(code: str, days: int) -> pd.DataFrame:
    """한 종목의 (index=날짜) 상장주식수·거래대금·종가·NAV — 캐시에서만 읽는다.

    상장 기간이 짧거나 캐시가 모자라면 있는 만큼만 돌려준다(화면이 'N일치' 표시).
    """
    code = str(code).strip()
    rows = []
    for bas_dd in cached_days()[-days:]:
        day = pd.read_csv(_day_path(bas_dd), dtype={"KRX코드": str})
        hit = day[day["KRX코드"].str.strip() == code]
        if hit.empty:
            continue
        record = hit.iloc[0].to_dict()
        record["날짜"] = pd.Timestamp(bas_dd)
        rows.append(record)
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows).set_index("날짜").sort_index()
    for column in ("상장주식수", "거래대금", "종가", "NAV"):
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


def main() -> int:
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    have = len(cached_days())
    print(f"수급 캐시 채우기 — 최근 {days}거래일 (현재 {have}일 보유)")
    fetched = ensure_days(
        days, progress=lambda i, n, d: print(f"  [{i}/{n}] {d}", flush=True))
    print(f"새로 받음 {fetched}일 / 캐시 합계 {len(cached_days())}일 → {CACHE_DIR}")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(BASE / "src"))
    from console import use_utf8_stdout   # 콘솔이 cp949여도 안 죽게
    use_utf8_stdout()
    from corp_ca import apply as apply_corp_ca   # 회사 HTTPS 가로채기 뒤에서도 받게
    apply_corp_ca()
    sys.exit(main())
