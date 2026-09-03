# -*- coding: utf-8 -*-
"""한국투자증권 KIS Developers — KRX 웹 조회를 **계약 기반 공식 API** 로 대체한다.

왜 갈아탔나 (44단계-취소): KDM(data.krx.co.kr)이 자동화 대량 조회를 약관으로
금지하고 IP 를 차단했다. KIS 는 계좌 기반으로 **약관이 API 사용을 전제**하는
공식 경로라 이 문제가 없다. 잃었던 세 데이터가 전부 있다:

| 잃은 것 | KIS 엔드포인트 | TR |
|---|---|---|
| 구성종목(PDF, krx_pdf.py) | `/uapi/etfetn/v1/quotations/inquire-component-stock-price` | FHKST121600C0 |
| NAV 시계열 (nav_erosion) | `/uapi/etfetn/v1/quotations/nav-comparison-daily-trend` | FHPST02440200 |
| 투자자별 (investor_flow) | `/uapi/domestic-stock/v1/quotations/inquire-investor` | FHKST01010900 |

- 앱키는 `.env` 의 `KIS_APP_KEY` / `KIS_APP_SECRET` (KIS Developers 에서 발급)
- 호출 제한: 실전 초당 20건 — `PAUSE` 로 그 절반만 쓴다(몰아치지 않는다)

### 세션 간 공유 — `shared-data` 규약을 따른다 (vibe_inv 공통)
`D:\\MJLEE\\vibe_inv\\shared-data\\` 는 peak·bottom-research·breadth 와 이
프로젝트가 같이 쓰는 스테이징이다 (규약 전문: peak `HANDOVER.md` §7,
`shared-data/README.md`). 여기서 따르는 것:

- **토큰은 반드시 공유** — `shared-data/kis_token.json`
  ({access_token, expires_at}). 같은 앱키로 재발급하면 **기존 토큰이 즉시
  무효화**되고 발급도 분당 1회 제한이라, 세션마다 발급하면 서로를 죽인다.
  재발급은 `kis_token.lock` 을 잡고 한다 (90초 넘은 잠금은 죽은 세션의 것으로
  회수, 잠금을 기다린 뒤엔 다른 세션이 이미 발급했는지 재확인).
- **하루 단위 종목 캐시는 이 저장소 안** `data/kis_cache/` (gitignore).
  스테이징에는 원천 시리즈(`<원천>_<시리즈>_<주기>.parquet`+메타)만 두는
  규약이라, 종목별 낱개 CSV 는 프로젝트 캐시로 남긴다.
- 경로 바꾸기: 환경변수 `SHARED_DATA_DIR` / `KIS_CACHE_DIR`

실행: venv\\Scripts\\python src\\kis_api.py           (연결 확인 — KODEX 200 3종 조회)
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import json
import os
import sys
import time

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))

import env_keys                    # noqa: E402

BASE = Path(__file__).resolve().parent.parent
# 세션 간 공유 스테이징 (shared-data 규약 — 모듈 머리말)
SHARED_ROOT = Path(os.environ.get("SHARED_DATA_DIR",
                                  BASE.parent / "shared-data"))
TOKEN_CACHE = SHARED_ROOT / "kis_token.json"
TOKEN_LOCK = SHARED_ROOT / "kis_token.lock"
# 종목별 하루 캐시 — 프로젝트 전용 (스테이징 명명 규칙에 안 맞는 낱개 파일)
SHARED_CACHE = Path(os.environ.get("KIS_CACHE_DIR",
                                   BASE / "data" / "kis_cache"))

PROD_URL = "https://openapi.koreainvestment.com:9443"
# 문서상 한도는 초당 20건(=0.05초)이지만 **실제는 그보다 빡빡하다.**
# 전 종목 수집 실측(46단계): 0.12초에서 실패 184/1,194(20%) → 0.5초에서 32건(2.7%).
# 낱개 조회는 0.3초면 충분하고, 전수 수집은 호출부에서 더 올린다.
PAUSE = 0.3

KEY_NAMES = ("KIS_APP_KEY", "KIS_APP_SECRET")


class KisKeyMissing(RuntimeError):
    """앱키가 없다 — 화면·CLI 가 '무엇을 넣어야 하는지' 안내할 때 잡는다."""


def _keys() -> tuple[str, str]:
    missing = env_keys.missing(*KEY_NAMES)
    if missing:
        raise KisKeyMissing(
            f".env 에 {', '.join(missing)} 가 없습니다. "
            "KIS Developers(apiportal.koreainvestment.com)에서 발급해 넣으세요.")
    return os.environ["KIS_APP_KEY"], os.environ["KIS_APP_SECRET"]


# ── 토큰 (shared-data 규약 — peak/verify/marketdata.py 의 kis_token 과 동일 동작) ──
def _read_cached_token() -> str | None:
    """공유 토큰. 만료 30분 전부터는 없는 것으로 친다."""
    try:
        data = json.loads(TOKEN_CACHE.read_text(encoding="utf-8"))
        expires = datetime.fromisoformat(data["expires_at"])
        if expires - datetime.now() > timedelta(minutes=30):
            return data["access_token"]
    except (OSError, KeyError, ValueError, json.JSONDecodeError):
        pass
    return None


def _acquire_lock(timeout: float = 70.0) -> bool:
    """발급이 분당 1회 제한이라 잠금 대기 상한을 그보다 길게 둔다."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            fd = os.open(TOKEN_LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return True
        except FileExistsError:
            try:                    # 죽은 세션이 남긴 잠금은 90초 뒤 회수
                if time.time() - TOKEN_LOCK.stat().st_mtime > 90:
                    TOKEN_LOCK.unlink(missing_ok=True)
                    continue
            except OSError:
                pass
            time.sleep(1.0)
    return False


def access_token(session: requests.Session | None = None) -> str:
    """공유 토큰 반환. 30분 이상 남았으면 재사용, 아니면 잠금 잡고 재발급.

    **같은 앱키로 재발급하면 기존 토큰이 즉시 무효화된다** — 다른 세션
    (peak·bottom-research·breadth)과 한 파일을 쓰는 이유다. 발급마다
    카카오 알림톡도 온다(공식 동작).
    """
    cached = _read_cached_token()
    if cached:
        return cached
    if not _acquire_lock():
        raise RuntimeError("KIS 토큰 잠금을 얻지 못했다 — 다른 세션이 발급 중인지 확인")
    try:
        # 잠금을 기다리는 사이 다른 세션이 발급해 뒀을 수 있다
        cached = _read_cached_token()
        if cached:
            return cached
        app_key, app_secret = _keys()
        sess = session or requests
        res = sess.post(f"{PROD_URL}/oauth2/tokenP", json={
            "grant_type": "client_credentials",
            "appkey": app_key, "appsecret": app_secret,
        }, timeout=20)
        res.raise_for_status()
        body = res.json()
        token = body["access_token"]
        # 만료 5분 전을 만료로 적는다 (경계에서의 무효 토큰 사용 방지)
        expires = datetime.now() + timedelta(
            seconds=int(body.get("expires_in", 86400)) - 300)
        TOKEN_CACHE.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_CACHE.write_text(json.dumps({
            "access_token": token,
            "expires_at": expires.isoformat(timespec="seconds"),
        }), encoding="utf-8")
        return token
    finally:
        TOKEN_LOCK.unlink(missing_ok=True)


def _headers(tr_id: str, session: requests.Session | None = None) -> dict:
    app_key, app_secret = _keys()
    return {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {access_token(session)}",
        "appkey": app_key, "appsecret": app_secret,
        "tr_id": tr_id, "custtype": "P",
    }


def _get(url: str, tr_id: str, params: dict,
         session: requests.Session | None = None) -> dict:
    """공통 GET. rt_cd 가 0 이 아니면 메시지를 담아 던진다(조용히 빈 값 금지)."""
    sess = session or requests
    res = sess.get(f"{PROD_URL}{url}", headers=_headers(tr_id, session),
                   params=params, timeout=20)
    res.raise_for_status()
    body = res.json()
    if str(body.get("rt_cd", "")) != "0":
        raise RuntimeError(
            f"KIS 오류 rt_cd={body.get('rt_cd')} msg_cd={body.get('msg_cd')} "
            f"{body.get('msg1', '').strip()} (tr={tr_id})")
    return body


# ── 공유 캐시 (하루 단위) ────────────────────────────────
# 구성종목·투자자별은 하루에 한 번이면 충분한 값이다. 같은 날 같은 종목을
# (다른 프로젝트가라도) 또 부르면 API 대신 여기서 읽는다.
def _cache_path(kind: str, code: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d")
    return SHARED_CACHE / f"{kind}_{code}_{stamp}.csv"


def _cache_read(kind: str, code: str,
                dates: tuple[str, ...] = ()) -> pd.DataFrame | None:
    path = _cache_path(kind, code)
    if not path.exists():
        return None
    frame = pd.read_csv(path, dtype={"종목코드": str, "구성종목코드": str},
                        parse_dates=[c for c in dates])
    return frame


def _cache_write(kind: str, code: str, frame: pd.DataFrame) -> None:
    try:
        SHARED_CACHE.mkdir(parents=True, exist_ok=True)
        frame.to_csv(_cache_path(kind, code), index=False, encoding="utf-8-sig")
    except OSError:
        pass                        # 캐시는 편의일 뿐 — 못 써도 조회는 성공이다


# ── ① 구성종목 (krx_pdf.py 대체) ─────────────────────────
# output2 태그 → 우리 컬럼. krx_PDF CSV 와 이어 쓸 수 있게 이름을 맞춘다
COMPONENT_COLS = {
    "stck_shrn_iscd": "구성종목코드",
    "hts_kor_isnm": "구성종목명",
    "stck_prpr": "현재가",
    "etf_cnfg_issu_avls": "구성금액",       # 구성종목 시가총액(원)
    "etf_vltn_amt": "평가금액",
    "etf_cnfg_issu_rlim": "비중",
}


def component_stocks(code: str, session: requests.Session | None = None,
                     use_cache: bool = True) -> pd.DataFrame:
    """한 ETF 의 구성종목 — **상위 30개까지만 온다** (HTS [0245]와 같은 한계).

    실측(2026-08-15): 국내 주식형만 채워진다. KODEX 200 은 30행(비중합 84.5%),
    30종목 이하면 전부(SOL 조선TOP3플러스 13행 100%). **채권형·해외주식형은
    0행**이고 합성형은 원화 담보(단기채)만 온다 — 전체 PDF 의 대체가 아니라
    'TOP30 갱신' 용도다. 전체 구성은 8/13 KRX PDF 스냅샷이 남아 있다.
    """
    if use_cache:
        cached = _cache_read("구성종목", code)
        if cached is not None:
            return cached
    body = _get("/uapi/etfetn/v1/quotations/inquire-component-stock-price",
                "FHKST121600C0",
                {"FID_COND_MRKT_DIV_CODE": "J",
                 "FID_INPUT_ISCD": str(code).strip(),
                 "FID_COND_SCR_DIV_CODE": "11216"}, session)
    rows = body.get("output2") or []
    if not rows:
        return pd.DataFrame(columns=["종목코드", *COMPONENT_COLS.values()])
    frame = pd.DataFrame(rows)
    keep = {tag: name for tag, name in COMPONENT_COLS.items()
            if tag in frame.columns}
    out = frame[list(keep)].rename(columns=keep)
    out.insert(0, "종목코드", str(code).strip())
    for column in ("현재가", "구성금액", "평가금액", "비중"):
        if column in out.columns:
            out[column] = pd.to_numeric(
                out[column].astype(str).str.replace(",", "", regex=False),
                errors="coerce")
    if use_cache and not out.empty:
        _cache_write("구성종목", code, out)
    return out


# ── ①-보강 배수 (레버리지·인버스) ────────────────────────
# `etf_trc_ert_mltp` = ETF 추적수익률 배수. **이것이 유일한 데이터 근거**다 —
# 6단계에서 확인했듯 KRX 의 자산분류·운용방식·기초지수명은 레버리지를 구분하지
# 못한다(KODEX 레버리지와 KODEX 200 이 전 칸 동일, 기초지수도 똑같이 '코스피 200').
# 실측: KODEX 레버리지 2.00 / KODEX 인버스 -1.00 / KODEX 200 1.00.
PRICE_INFO_COLS = {
    "etf_trc_ert_mltp": "배수",
    "etf_rprs_bstp_kor_isnm": "대표지수",
    "etf_cnfg_issu_cnt": "구성종목수",
    "etf_ntas_ttam": "순자산총액",
    "etf_crcl_stcn": "상장주식수",
}


def etf_price_info(code: str, session: requests.Session | None = None,
                   use_cache: bool = True) -> dict:
    """ETF 현재가 화면의 **정적 성격 칸**만 뽑는다 (배수·대표지수·구성종목수).

    시세 칸은 다른 경로(Open API 일별)가 이미 대고 있어 여기서는 안 쓴다.
    """
    if use_cache:
        cached = _cache_read("배수", code)
        if cached is not None and not cached.empty:
            return cached.iloc[0].to_dict()
    body = _get("/uapi/etfetn/v1/quotations/inquire-price", "FHPST02400000",
                {"FID_COND_MRKT_DIV_CODE": "J",
                 "FID_INPUT_ISCD": str(code).strip()}, session)
    raw = body.get("output") or {}
    if not raw:
        return {}
    out = {"종목코드": str(code).strip()}
    for tag, name in PRICE_INFO_COLS.items():
        value = raw.get(tag)
        if value is None or str(value).strip() == "":
            continue
        if name in ("배수", "구성종목수", "순자산총액", "상장주식수"):
            out[name] = pd.to_numeric(
                str(value).replace(",", ""), errors="coerce")
        else:
            out[name] = str(value).strip()
    # ⚠ 배수 0 은 **'0배'가 아니라 미제공**이다. 실측(2026-08-15) 31종목이 0 으로
    # 왔는데 그 안에 `SOL 미국테크TOP10인버스(합성)` 가 있었다 — 명백한 인버스다.
    # 0 을 값으로 쓰면 인버스를 일반으로 오표기하고, 1 로 메우면 더 나쁘다.
    # 이 31종목은 `대표지수` 도 함께 비어 있었다(메타 자체가 없는 종목들).
    if out.get("배수") == 0:
        out.pop("배수")
    if use_cache and out:
        _cache_write("배수", code, pd.DataFrame([out]))
    return out


# ── ② NAV 일별 (nav_erosion 의 KRX 조회 대체) ────────────
NAV_COLS = {
    "stck_bsop_date": "날짜",
    "stck_clpr": "종가",
    "nav": "NAV",
    "dprt": "괴리율",
}


def nav_daily(code: str, start: str, end: str,
              session: requests.Session | None = None) -> pd.DataFrame:
    """한 ETF 의 일별 종가·NAV·괴리율. 호출당 최대 100건(약 5개월).

    더 긴 구간은 100건씩 나눠 부른다 — `nav_history()`.
    """
    body = _get("/uapi/etfetn/v1/quotations/nav-comparison-daily-trend",
                "FHPST02440200",
                {"FID_COND_MRKT_DIV_CODE": "J",
                 "FID_INPUT_ISCD": str(code).strip(),
                 "FID_INPUT_DATE_1": start, "FID_INPUT_DATE_2": end}, session)
    rows = body.get("output") or []
    if not rows:
        return pd.DataFrame(columns=NAV_COLS.values())
    frame = pd.DataFrame(rows)
    keep = {tag: name for tag, name in NAV_COLS.items() if tag in frame.columns}
    out = frame[list(keep)].rename(columns=keep)
    out["날짜"] = pd.to_datetime(out["날짜"], format="%Y%m%d", errors="coerce")
    for column in ("종가", "NAV", "괴리율"):
        if column in out.columns:
            out[column] = pd.to_numeric(
                out[column].astype(str).str.replace(",", "", regex=False),
                errors="coerce")
    return out.dropna(subset=["날짜"]).sort_values("날짜").reset_index(drop=True)


def nav_history(code: str, start: str, end: str,
                session: requests.Session | None = None) -> pd.DataFrame:
    """긴 구간 NAV — 100건 제한을 넘도록 뒤에서부터 나눠 받는다."""
    frames = []
    cursor_end = pd.Timestamp(end)
    start_ts = pd.Timestamp(start)
    while cursor_end >= start_ts:
        # 100 거래일 ≈ 145 자연일. 여유 있게 140일씩 자른다
        cursor_start = max(cursor_end - pd.Timedelta(days=140), start_ts)
        got = nav_daily(code, cursor_start.strftime("%Y%m%d"),
                        cursor_end.strftime("%Y%m%d"), session)
        if not got.empty:
            frames.append(got)
        cursor_end = cursor_start - pd.Timedelta(days=1)
        time.sleep(PAUSE)
    if not frames:
        return pd.DataFrame(columns=NAV_COLS.values())
    return (pd.concat(frames, ignore_index=True)
            .drop_duplicates("날짜").sort_values("날짜").reset_index(drop=True))


# ── ③ 투자자별 (investor_flow 대체) ──────────────────────
INVESTOR_COLS = {
    "stck_bsop_date": "날짜",
    "stck_clpr": "종가",
    "prsn_ntby_qty": "개인_순매수량",
    "frgn_ntby_qty": "외국인_순매수량",
    "orgn_ntby_qty": "기관_순매수량",
    "prsn_ntby_tr_pbmn": "개인_순매수대금",
    "frgn_ntby_tr_pbmn": "외국인_순매수대금",
    "orgn_ntby_tr_pbmn": "기관_순매수대금",
}


def investor_daily(code: str, session: requests.Session | None = None,
                   use_cache: bool = True) -> pd.DataFrame:
    """일별 투자자별 순매수 (개인·외국인·기관). 최근 30거래일이 온다.

    당일분은 장 종료 후에 잡힌다. 세부 분류(증권·투신·보험 등)까지 필요하면
    `investor_detail()` 쪽이다.
    """
    if use_cache:
        cached = _cache_read("투자자별", code, dates=("날짜",))
        if cached is not None:
            return cached
    body = _get("/uapi/domestic-stock/v1/quotations/inquire-investor",
                "FHKST01010900",
                {"FID_COND_MRKT_DIV_CODE": "J",
                 "FID_INPUT_ISCD": str(code).strip()}, session)
    rows = body.get("output") or []
    if not rows:
        return pd.DataFrame(columns=INVESTOR_COLS.values())
    frame = pd.DataFrame(rows)
    keep = {tag: name for tag, name in INVESTOR_COLS.items()
            if tag in frame.columns}
    out = frame[list(keep)].rename(columns=keep)
    out["날짜"] = pd.to_datetime(out["날짜"], format="%Y%m%d", errors="coerce")
    for column in out.columns:
        if column != "날짜":
            out[column] = pd.to_numeric(
                out[column].astype(str).str.replace(",", "", regex=False),
                errors="coerce")
    out = out.dropna(subset=["날짜"]).sort_values("날짜").reset_index(drop=True)
    if use_cache and not out.empty:
        _cache_write("투자자별", code, out)
    return out


# ── ③-보강 세부 투자자 (HTS [0416] — 42단계 화면의 재료) ──
# 실측(2026-08-15): 증권(scrt)·투신(ivtr)·사모펀드(pe_fund)·은행(bank)·보험(insu)·
# 종금(mrbn)·기금(fund)·기타법인(etc_corp)·등록/비등록 외국인까지, 매도/매수/순매수의
# 수량·대금이 전부 온다. 한 번에 최근 30거래일.
DETAIL_GROUPS = {
    "prsn": "개인", "frgn": "외국인", "orgn": "기관합계",
    "scrt": "금융투자", "ivtr": "투신", "fund": "기금",
    "bank": "은행", "insu": "보험", "mrbn": "종금",
    "pe_fund": "사모", "etc_corp": "기타법인",
}


def investor_detail(code: str, base_date: str | None = None,
                    session: requests.Session | None = None,
                    use_cache: bool = True) -> pd.DataFrame:
    """세부 투자자별 일별 매매 — 그룹마다 매도/매수/순매수 대금(백만원).

    base_date(YYYYMMDD)를 주면 그 날짜까지의 최근 30거래일이 온다(과거 백필용).
    """
    cache_key = f"{code}_{base_date}" if base_date else code
    if use_cache:
        cached = _cache_read("투자자세부", cache_key, dates=("날짜",))
        if cached is not None:
            return cached
    body = _get("/uapi/domestic-stock/v1/quotations/investor-trade-by-stock-daily",
                "FHPTJ04160001",
                {"FID_COND_MRKT_DIV_CODE": "J",
                 "FID_INPUT_ISCD": str(code).strip(),
                 "FID_INPUT_DATE_1": base_date
                 or datetime.now().strftime("%Y%m%d"),
                 "FID_ORG_ADJ_PRC": "", "FID_ETC_CLS_CODE": ""}, session)
    rows = body.get("output2") or []
    if not rows:
        return pd.DataFrame(columns=["날짜", "종가"])
    frame = pd.DataFrame(rows)
    out = pd.DataFrame()
    out["날짜"] = pd.to_datetime(frame["stck_bsop_date"], format="%Y%m%d",
                                 errors="coerce")
    out["종가"] = pd.to_numeric(frame.get("stck_clpr"), errors="coerce")
    for tag, name in DETAIL_GROUPS.items():
        # 대금(pbmn)과 수량(vol/qty)을 둘 다 담는다 — HTS 화면은 주 수량 기준이다.
        # ⚠ 순매수 수량의 접미사가 그룹마다 다르다: 대부분 `_ntby_qty` 인데
        #   사모(pe_fund)·기타법인(etc_corp) 등은 `_ntby_vol` 이다 — 실측으로 확인.
        for suffixes, korean in ((("seln_tr_pbmn",), "매도대금"),
                                 (("shnu_tr_pbmn",), "매수대금"),
                                 (("ntby_tr_pbmn",), "순매수대금"),
                                 (("seln_vol",), "매도량"),
                                 (("shnu_vol",), "매수량"),
                                 (("ntby_qty", "ntby_vol"), "순매수량")):
            column = next((f"{tag}_{s}" for s in suffixes
                           if f"{tag}_{s}" in frame.columns), None)
            if column:
                out[f"{name}_{korean}"] = pd.to_numeric(
                    frame[column].astype(str).str.replace(",", "", regex=False),
                    errors="coerce")
    out = out.dropna(subset=["날짜"]).sort_values("날짜").reset_index(drop=True)
    if use_cache and not out.empty:
        _cache_write("투자자세부", cache_key, out)
    return out


# ── 연결 확인 ────────────────────────────────────────────
def main() -> int:
    try:
        _keys()
    except KisKeyMissing as exc:
        print(f"✗ {exc}")
        return 1
    code = "069500"          # KODEX 200 — 어디서나 있는 종목으로 확인
    print("① 구성종목시세…")
    comp = component_stocks(code)
    print(f"   {len(comp)}종목 (상위 5)")
    print(comp.head().to_string(index=False))
    time.sleep(PAUSE)
    print("② NAV 일별 (최근 2주)…")
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=14)).strftime("%Y%m%d")
    nav = nav_daily(code, start, end)
    print(nav.tail(5).to_string(index=False))
    time.sleep(PAUSE)
    print("③ 투자자별…")
    inv = investor_daily(code)
    print(inv.tail(5).to_string(index=False))
    print("\n✓ 세 엔드포인트 모두 정상")
    return 0


if __name__ == "__main__":
    import corp_ca
    from console import use_utf8_stdout

    corp_ca.apply()
    use_utf8_stdout()
    sys.exit(main())
