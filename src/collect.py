# -*- coding: utf-8 -*-
"""
1단계-수집: 세이브로 ETF 분배금지급현황 데이터 수집
- 데이터 소스: seibro.or.kr > ETF > 권리행사정보 > 분배금지급현황
- 방식: 화면 조회 시 호출되는 내부 요청(callServletService.jsp)을 그대로 재현
  * 세션 쿠키 + Referer 헤더가 있어야 서버가 응답함
  * START_PAGE = 시작 '행' 번호(1부터), 한 요청당 30행 반환 → 30씩 늘려가며 전체 수집
- 결과: data/raw/seibro_분배금_YYYYMMDD.csv 로 원본 저장 (가공 없이 그대로)
"""
from datetime import date, datetime
from pathlib import Path
import sys
import xml.etree.ElementTree as ET
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import pandas as pd

# ── 경로 설정 ─────────────────────────────────────────────
BASE = Path(__file__).resolve().parent.parent      # 프로젝트 루트
RAW_DIR = BASE / "data" / "raw"
RAW_DIR.mkdir(parents=True, exist_ok=True)

# ── 세이브로 요청 정보 (브라우저 F12에서 캡처해 확인한 값) ──
PAGE_URL = ("https://seibro.or.kr/websquare/control.jsp"
            "?w2xPath=/IPORTAL/user/etf/BIP_CNTS06030V.xml&menuNo=179")
API_URL = "https://seibro.or.kr/websquare/engine/proworks/callServletService.jsp"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36")

# 서버가 돌려주는 XML 태그명 → 우리가 쓸 한글 컬럼명
COLS = {
    "ISIN": "ISIN",
    "KOR_SECN_NM": "종목명",
    "REP_SECN_NM": "운용사",
    "ETF_SORT_NM": "유형",
    "ETF_SORT_CD": "유형코드",
    "RGT_STD_DT": "지급기준일",          # 권리기준일(YYYYMMDD)
    "TH1_PAY_TERM_BEGIN_DT": "실지급일",  # YYYYMMDD
    "BUNBE": "분배율",
    "ESTM_STDPRC": "주당분배금",
    "TAXSTD": "과표기준가",
    "RGT_RSN_DTAIL_NM": "배당구분",       # 이익분배 / 청산분배
    "ISSUCO_CUSTNO": "발행사번호",
}


def make_body(start_row: int, from_dt: str, to_dt: str) -> str:
    """조회 요청 XML 본문 만들기. start_row 부터 30행을 요청."""
    end_row = start_row + 29
    return (
        '<reqParam action="exerInfoDtramtPayStatPlist" '
        'task="ksd.safe.bip.cnts.etf.process.EtfExerInfoPTask">'
        '<MENU_NO value="179"/><CMM_BTN_ABBR_NM value=""/>'
        '<W2XPATH value="/IPORTAL/user/etf/BIP_CNTS06030V.xml"/>'
        '<etf_sort_level_cd value="0"/><etf_big_sort_cd value=""/>'
        f'<START_PAGE value="{start_row}"/><END_PAGE value="{end_row}"/>'
        '<etf_sort_cd value=""/><isin value=""/><mngco_custno value=""/>'
        '<RGT_RSN_DTAIL_SORT_CD value=""/>'
        f'<fromRGT_STD_DT value="{from_dt}"/><toRGT_STD_DT value="{to_dt}"/>'
        '</reqParam>'
    )


def make_count_body(from_dt: str, to_dt: str) -> str:
    """전체 건수 조회용 XML 본문 (action 끝이 ...Cnt)."""
    return make_body(1, from_dt, to_dt).replace(
        "exerInfoDtramtPayStatPlist", "exerInfoDtramtPayStatPlistCnt")


def parse_rows(xml_text: str) -> list[dict]:
    """응답 XML에서 <data><result> 안의 값들을 dict 목록으로 변환."""
    root = ET.fromstring(xml_text)
    rows = []
    for result in root.findall(".//data/result"):
        row = {}
        for child in result:
            row[child.tag] = child.get("value")
        rows.append(row)
    return rows


def parse_total_count(xml_text: str) -> int:
    """건수 응답의 LIST_CNT를 검증해 정수로 반환한다."""
    root = ET.fromstring(xml_text)
    node = root.find(".//LIST_CNT")
    if node is None or node.get("value") is None:
        raise ValueError("세이브로 응답에 LIST_CNT가 없습니다.")
    try:
        return int(node.get("value"))
    except (TypeError, ValueError) as exc:
        raise ValueError("세이브로 LIST_CNT가 올바른 정수가 아닙니다.") from exc


def validate_date_range(from_dt: str, to_dt: str) -> None:
    """YYYYMMDD 형식·실재 날짜·기간 순서를 검증한다."""
    try:
        start = datetime.strptime(from_dt, "%Y%m%d").date()
        end = datetime.strptime(to_dt, "%Y%m%d").date()
    except (TypeError, ValueError) as exc:
        raise ValueError("날짜는 실재하는 YYYYMMDD 형식이어야 합니다.") from exc
    if start > end:
        raise ValueError("시작 날짜는 종료 날짜보다 늦을 수 없습니다.")


def ensure_collection_complete(received: int, expected: int, unique: int | None = None) -> None:
    """서버가 고지한 원시 건수보다 적게 받은 수집을 실패 처리한다."""
    unique = received if unique is None else unique
    if received != expected or unique != expected:
        raise RuntimeError(
            f"세이브로 수집이 불완전합니다: 응답 {received}건, 고유 {unique}건, 기대 {expected}건"
        )


def rolling_year_start(today: date, years: int = 1) -> str:
    """윤년을 포함해 기준일의 N년 전 날짜를 YYYYMMDD로 반환한다."""
    return (pd.Timestamp(today) - pd.DateOffset(years=years)).strftime("%Y%m%d")


def build_session() -> requests.Session:
    """타임아웃성 HTTP 오류에 bounded retry를 적용한 세션을 만든다."""
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        status=3,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "POST"}),
        raise_on_status=False,
    )
    session = requests.Session()
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({"User-Agent": UA})
    return session


def collect(from_dt: str, to_dt: str) -> pd.DataFrame:
    """세이브로에서 from_dt~to_dt 기간의 분배금 지급현황 전체를 수집."""
    validate_date_range(from_dt, to_dt)
    sess = build_session()

    # 1) 페이지를 먼저 열어 세션 쿠키 확보
    page_response = sess.get(PAGE_URL, timeout=20)
    page_response.raise_for_status()

    post_headers = {"Content-Type": "application/xml", "Referer": PAGE_URL}

    # 2) 전체 건수 확인
    r = sess.post(API_URL, data=make_count_body(from_dt, to_dt).encode("utf-8"),
                  headers=post_headers, timeout=20)
    r.raise_for_status()
    total = parse_total_count(r.text)
    print(f"전체 건수: {total}건 (기간 {from_dt}~{to_dt})")

    # 3) 30행씩 끝까지 수집 (중복은 ISIN+지급기준일 기준으로 제거)
    all_rows, seen = [], set()
    received = 0
    start = 1
    while start <= total:
        r = sess.post(API_URL, data=make_body(start, from_dt, to_dt).encode("utf-8"),
                      headers=post_headers, timeout=20)
        r.raise_for_status()
        rows = parse_rows(r.text)
        if not rows:
            break
        received += len(rows)
        for row in rows:
            key = tuple(sorted(row.items()))
            if key not in seen:
                seen.add(key)
                all_rows.append(row)
        print(f"  수집 {len(all_rows)}/{total}", end="\r")
        start += 30
    ensure_collection_complete(received, total, len(seen))
    print(f"\n수집 완료: {len(all_rows)}건")

    # 4) 한글 컬럼명으로 정리 (원본 값은 그대로 유지)
    df = pd.DataFrame(all_rows)
    df = df.reindex(columns=list(COLS.keys()))   # 컬럼 순서 고정
    df = df.rename(columns=COLS)
    return df


YEARS = 2       # 분배금성장률(직전 12개월 vs 그 앞 12개월)을 내려면 2년치가 필요하다


def main():
    today = date.today()
    to_dt = today.strftime("%Y%m%d")
    # 기본 2년치. 분배주기 판정은 여전히 최근 12개월만 쓰고(CLAUDE.md 규칙),
    # 그 앞 12개월은 분배금이 늘었는지 줄었는지 보는 데만 쓴다.
    # 뒤에 숫자를 주면 기간을 바꿀 수 있다: python src/collect.py 1
    years = int(sys.argv[1]) if len(sys.argv) > 1 else YEARS
    from_dt = rolling_year_start(today, years)
    print(f"수집 기간: 최근 {years}년")

    df = collect(from_dt, to_dt)

    out = RAW_DIR / f"seibro_분배금_{to_dt}.csv"
    df.to_csv(out, index=False, encoding="utf-8-sig")   # 엑셀에서 한글 안 깨지게
    print(f"저장: {out}")
    print("\n[확인용 상위 5행]")
    print(df.head().to_string())


if __name__ == "__main__":
    from console import use_utf8_stdout   # 콘솔이 cp949여도 안 죽게
    use_utf8_stdout()
    from corp_ca import apply as apply_corp_ca   # 회사 HTTPS 가로채기 뒤에서도 받게
    apply_corp_ca()
    sys.exit(main())
