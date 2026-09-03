# -*- coding: utf-8 -*-
"""세이브로 **펀드별분배금지급내역** 수집 — ETF 화면에 없는 분배 상세를 받는다.

왜 화면이 둘인가: `ETF > 권리행사정보 > 분배금지급현황`(collect.py 가 쓰는 것)은
칸이 12개뿐이다. 같은 분배를 **펀드 쪽 화면**에서 보면 칸이 더 있다 —
세금구분·배당확정여부·현금배당방법·총분배금·지급기간. ETF 도 펀드라 여기서 조회된다.

- 화면: `펀드 > 권리행사정보 > 펀드별분배금지급내역` (BIP_CNTS05008V, menuNo=152)
- action `exerFundByDtrPaySkedulPList` / task `ksd.safe.bip.cnts.Fund.process.FundExerPTask`
- **ISIN 으로 한 종목씩** 조회한다 (collect.py 처럼 기간 전체를 한 번에 못 받는다)

화면 정의 XML(`/IPORTAL/user/fund/BIP_CNTS05008V.xml`)에 action·task·파라미터가
그대로 적혀 있다. 다음에 세이브로 다른 화면을 뚫을 때도 그 XML 부터 받으면 된다.

실행: venv\\Scripts\\python src\\fund_dist.py          (2년치, 분배 이력 있는 국내 ETF 전체)
      venv\\Scripts\\python src\\fund_dist.py 30       (앞 30종목만 — 시험용)
"""
from datetime import date
from pathlib import Path
import sys
import time
import xml.etree.ElementTree as ET

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))

BASE = Path(__file__).resolve().parent.parent
RAW_DIR = BASE / "data" / "raw"
RAW_DIR.mkdir(parents=True, exist_ok=True)

PAGE_URL = ("https://seibro.or.kr/websquare/control.jsp"
            "?w2xPath=/IPORTAL/user/fund/BIP_CNTS05008V.xml&menuNo=152")
API_URL = "https://seibro.or.kr/websquare/engine/proworks/callServletService.jsp"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36")

TASK = "ksd.safe.bip.cnts.Fund.process.FundExerPTask"
ACTION = "exerFundByDtrPaySkedulPList"

PAUSE = 0.25        # 호출 간격(초) — 몰아치지 않는다
PAGE_ON_CNT = 30    # 한 종목이 2년간 30회를 넘기지 않는다(위클리가 없으므로)

# 응답 태그 → 우리 컬럼. ★ 는 ETF 화면(collect.py)에 **없던** 값이다
COLS = {
    "RGT_STD_DT": "지급기준일",
    "RGT_RSN_DTAIL_SORT_NM": "배당구분",
    "CASH_ALOC_AMT": "주당분배금",
    "CASH_ALOC_RATIO": "분배율",
    "SETACC_TAXSTD": "과표기준가",
    "SETACC_STDPRC": "결산기준가",        # ★
    "TAX_TPNM": "세금구분",               # ★
    "FIX_TPNM": "배당확정여부",           # ★
    "ALOC_WHNM": "현금배당방법",          # ★
    "TOT_DIV_PAY_AMT": "총분배금",        # ★ 종목 전체 지급액(원)
    "PAY_TERM": "지급기간",               # ★
    "CLERDIV_VAL": "청산분배금기준",
    "CLER_NOS": "청산차수",
}


def build_session() -> requests.Session:
    """세션 쿠키를 확보한 세션. 쿠키 없이 POST 하면 서버가 안 받는다."""
    sess = requests.Session()
    sess.headers.update({"User-Agent": UA})
    sess.get(PAGE_URL, timeout=20).raise_for_status()
    return sess


def make_body(isin: str, from_dt: str, to_dt: str, page: int = 1) -> bytes:
    return (f'<reqParam action="{ACTION}" task="{TASK}">'
            f'<ISIN value="{isin}"/>'
            f'<START_DT value="{from_dt}"/><END_DT value="{to_dt}"/>'
            f'<PAGE_NUM value="{page}"/><PAGE_ON_CNT value="{PAGE_ON_CNT}"/>'
            "</reqParam>").encode("utf-8")


def parse_rows(xml_text: str) -> list[dict]:
    """응답 XML → dict 목록. 태그를 통째로 담는다(나중에 칸이 늘어도 안 잃는다)."""
    root = ET.fromstring(xml_text)
    return [{child.tag: child.get("value") for child in result}
            for result in root.findall(".//data/result")]


def fetch_one(sess: requests.Session, isin: str,
              from_dt: str, to_dt: str) -> list[dict]:
    """한 종목의 분배 내역. 실패하면 빈 목록(한 종목 때문에 전체가 멈추지 않게)."""
    headers = {"Content-Type": "application/xml", "Referer": PAGE_URL}
    response = sess.post(API_URL, data=make_body(isin, from_dt, to_dt),
                         headers=headers, timeout=25)
    response.raise_for_status()
    return parse_rows(response.text)


def target_isins() -> pd.DataFrame:
    """분배 이력이 있는 국내 ETF 목록 (collect.py 수집분에서 가져온다)."""
    files = sorted(RAW_DIR.glob("seibro_분배금_*.csv"))
    if not files:
        raise FileNotFoundError(
            "data/raw 에 seibro_분배금_*.csv 가 없습니다. 먼저 collect.py 를 실행하세요.")
    frame = pd.read_csv(files[-1], dtype=str)
    return frame.drop_duplicates("ISIN")[["ISIN", "종목명"]].reset_index(drop=True)


def collect(from_dt: str, to_dt: str, limit: int | None = None) -> pd.DataFrame:
    """전 종목을 한 바퀴 돌며 분배 상세를 모은다."""
    targets = target_isins()
    if limit:
        targets = targets.head(limit)
    print(f"대상 {len(targets):,}종목 (기간 {from_dt}~{to_dt})")

    sess = build_session()
    rows: list[dict] = []
    failed: list[str] = []
    for i, item in enumerate(targets.itertuples(index=False), 1):
        try:
            got = fetch_one(sess, item.ISIN, from_dt, to_dt)
        except Exception:
            failed.append(item.ISIN)
            got = []
        for row in got:
            row["ISIN"] = item.ISIN
            row["종목명"] = item.종목명
            rows.append(row)
        if i % 25 == 0 or i == len(targets):
            print(f"  {i}/{len(targets)} — {len(rows):,}건", end="\r")
        time.sleep(PAUSE)

    print(f"\n수집 {len(rows):,}건 / 실패 {len(failed)}종목")
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows)
    keep = ["ISIN", "종목명"] + [tag for tag in COLS if tag in frame.columns]
    return frame[keep].rename(columns=COLS)


def latest_csv() -> Path | None:
    files = sorted(RAW_DIR.glob("seibro_분배상세_*.csv"))
    return files[-1] if files else None


def load_latest() -> pd.DataFrame:
    """저장된 분배 상세. 없으면 빈 표(대시보드는 이 칸 없이 그대로 돈다)."""
    path = latest_csv()
    if path is None:
        return pd.DataFrame(columns=["ISIN", "종목명", *COLS.values()])
    return pd.read_csv(path, dtype=str)


YEARS = 2       # collect.py 와 같은 기간을 본다


def main():
    limit = None
    if len(sys.argv) > 1:
        limit = int(sys.argv[1])
    today = date.today()
    from_dt = (pd.Timestamp(today) - pd.DateOffset(years=YEARS)).strftime("%Y%m%d")
    to_dt = today.strftime("%Y%m%d")

    frame = collect(from_dt, to_dt, limit)
    if frame.empty:
        print("수집된 행이 없습니다.")
        return 1

    out = RAW_DIR / f"seibro_분배상세_{to_dt}.csv"
    frame.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"저장: {out} ({len(frame):,}건)")

    # ★ 새로 받은 칸들이 실제로 값이 갈리는지 바로 보여준다
    for column in ("세금구분", "배당확정여부", "현금배당방법", "배당구분"):
        if column in frame.columns:
            counts = frame[column].fillna("(빈값)").value_counts()
            print(f"\n[{column}] 값 {len(counts)}종")
            print(counts.head(6).to_string())
    return 0


if __name__ == "__main__":
    import corp_ca            # 회사 보안장비 인증서 (집에서는 아무것도 안 한다)
    from console import use_utf8_stdout

    corp_ca.apply()
    use_utf8_stdout()
    sys.exit(main())
