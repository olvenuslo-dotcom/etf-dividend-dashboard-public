# -*- coding: utf-8 -*-
"""세이브로 **보유자산명세** 수집 — 실측 겹침 v1 의 재료 (40단계-조사에서 경로 확정).

한 종목당 두 요청:
  ① compstInfoHoldAssetList        — FICS 섹터별 비중 (ETF/시장/시장대비)
  ② compstInfoHoldAssetDetailList  — 상위 10 구성종목 실제 비중 (**TOP10 이 전부**다.
     페이징이 없는 게 아니라 화면에 "PDF 내 보유종목 TOP10"이라고 명시돼 있다)

- 화면: `ETF종합정보 > 보유자산명세` (BIP_CNTS06034V, menuNo=182)
- task `ksd.safe.bip.cnts.etf.process.EtfCompstInfoPTask`
- ② 는 STD_DT 가 필수라 ① 응답의 STD_DT(최근 기준일)를 받아서 넣는다
- 대상: KRX 정보 CSV 의 국내 ETF 전체 (분배 여부와 무관 — 겹침은 미분배도 본다)

중간에 죽어도 다시 실행하면 **이미 받은 종목은 건너뛰고 이어받는다**
(같은 날짜 CSV 에 이미 있는 ISIN 은 스킵. 100종목마다 저장).

실행: venv\\Scripts\\python src\\compst.py        (전 종목, 20분 안팎)
      venv\\Scripts\\python src\\compst.py 5      (앞 5종목만 — 시험용)
"""
from datetime import date
from pathlib import Path
import sys
import time
import xml.etree.ElementTree as ET

import truststore                # 회사 PC 프록시 인증서 → Windows 저장소로 검증
truststore.inject_into_ssl()     # (개인 노트북에서는 없어도 되지만 있어도 무해)
import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))

import etf_info                  # noqa: E402 - 국내 ETF 전체 목록(ISIN)

BASE = Path(__file__).resolve().parent.parent
RAW_DIR = BASE / "data" / "raw"
RAW_DIR.mkdir(parents=True, exist_ok=True)

PAGE_URL = ("https://seibro.or.kr/websquare/control.jsp"
            "?w2xPath=/IPORTAL/user/etf/BIP_CNTS06034V.xml&menuNo=182")
API_URL = "https://seibro.or.kr/websquare/engine/proworks/callServletService.jsp"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36")

TASK = "ksd.safe.bip.cnts.etf.process.EtfCompstInfoPTask"
ACTION_SECTOR = "compstInfoHoldAssetList"
ACTION_TOP10 = "compstInfoHoldAssetDetailList"

PAUSE = 0.25        # 호출 간격(초) — 몰아치지 않는다

# 응답 태그 → 우리 컬럼
SECTOR_COLS = {
    "STD_DT": "기준일",
    "INDTP_INDEX_CD": "섹터코드",
    "INDTP_INDEX_NM": "섹터명",
    "HOLD_IMPO": "ETF비중",       # 이 ETF 안에서 그 섹터가 차지하는 %
    "MART_IMPO": "시장비중",      # 시장 전체에서 그 섹터의 %
    "DEFRATE": "시장대비",        # ETF비중 - 시장비중
    "KOR_SECN_NM": "종목명_세이브로",
}
TOP10_COLS = {
    "G_STD_DT": "기준일",
    "G_RNUM": "순위",
    "G_VCTF_ISIN": "구성종목코드",   # KRX 6자리 단축코드
    "G_KOR_SECN_NM": "구성종목명",
    "G_VCTF_QTY": "주식수",
    "G_HOLD_IMPO": "비중",           # PDF 안에서 이 종목의 %
    "G_LDAY_CPRI": "전일종가",
}


def build_session() -> requests.Session:
    """세션 쿠키를 확보한 세션. 쿠키 없이 POST 하면 서버가 안 받는다."""
    sess = requests.Session()
    sess.headers.update({"User-Agent": UA})
    sess.get(PAGE_URL, timeout=20).raise_for_status()
    return sess


def make_body(action: str, isin: str, std_dt: str) -> bytes:
    return (f'<reqParam action="{action}" task="{TASK}">'
            f'<ISIN value="{isin}"/><STD_DT value="{std_dt}"/>'
            "</reqParam>").encode("utf-8")


def parse_rows(xml_text: str) -> list[dict]:
    """응답 XML → dict 목록. 태그를 통째로 담는다(나중에 칸이 늘어도 안 잃는다)."""
    root = ET.fromstring(xml_text)
    return [{child.tag: child.get("value") for child in result}
            for result in root.findall(".//data/result")]


def fetch(sess: requests.Session, action: str, isin: str,
          std_dt: str = "") -> list[dict]:
    headers = {"Content-Type": "application/xml", "Referer": PAGE_URL}
    response = sess.post(API_URL, data=make_body(action, isin, std_dt),
                         headers=headers, timeout=25)
    response.raise_for_status()
    return parse_rows(response.text)


def fetch_one(sess: requests.Session, isin: str) -> tuple[list[dict], list[dict]]:
    """한 종목의 (섹터 비중, TOP10). ② 는 ① 이 준 기준일이 있어야 조회된다."""
    sectors = fetch(sess, ACTION_SECTOR, isin)
    std_dt = next((r.get("STD_DT") for r in sectors if r.get("STD_DT")), "")
    top10 = fetch(sess, ACTION_TOP10, isin, std_dt) if std_dt else []
    return sectors, top10


def targets() -> pd.DataFrame:
    """국내 ETF 전체 (KRX 정보 CSV). 분배 이력 없는 종목도 겹침 대상이다."""
    info = etf_info.load_latest()
    got = info.dropna(subset=["ISIN"]).drop_duplicates("ISIN")
    return got[["ISIN", "종목명"]].reset_index(drop=True)


def to_frame(rows: list[dict], cols: dict) -> pd.DataFrame:
    out = pd.DataFrame(rows)
    if out.empty:
        return pd.DataFrame(columns=["ISIN", *cols.values()])
    keep = {tag: name for tag, name in cols.items() if tag in out.columns}
    return out[["ISIN", *keep.keys()]].rename(columns=keep)


def _paths() -> tuple[Path, Path, Path]:
    stamp = date.today().strftime("%Y%m%d")
    return (RAW_DIR / f"seibro_구성섹터_{stamp}.csv",
            RAW_DIR / f"seibro_구성TOP10_{stamp}.csv",
            RAW_DIR / f"seibro_구성없음_{stamp}.csv")


def _done_isins(*paths: Path) -> set[str]:
    """오늘 파일에 이미 들어간 ISIN — 다시 실행하면 건너뛴다(이어받기)."""
    done: set[str] = set()
    for p in paths:
        if p.exists():
            done |= set(pd.read_csv(p, dtype=str)["ISIN"].dropna())
    return done


def _append(frame: pd.DataFrame, path: Path):
    """CSV 에 덧붙인다. 파일이 없을 때만 헤더를 쓴다."""
    if frame.empty:
        return
    frame.to_csv(path, mode="a", header=not path.exists(),
                 index=False, encoding="utf-8-sig")


def collect(limit: int | None = None):
    frame = targets()
    if limit:
        frame = frame.head(limit)
    sector_path, top10_path, empty_path = _paths()
    done = _done_isins(sector_path, empty_path)
    todo = frame[~frame["ISIN"].isin(done)]
    print(f"대상 {len(frame):,}종목 / 이미 받음 {len(done):,} / "
          f"이번에 {len(todo):,}종목 (요청은 종목당 2회)")
    if todo.empty:
        return

    sess = build_session()
    sector_buf: list[dict] = []
    top10_buf: list[dict] = []
    empty_buf: list[dict] = []
    failed: list[str] = []

    def flush():
        _append(to_frame(sector_buf, SECTOR_COLS), sector_path)
        _append(to_frame(top10_buf, TOP10_COLS), top10_path)
        _append(pd.DataFrame(empty_buf), empty_path)
        sector_buf.clear(); top10_buf.clear(); empty_buf.clear()

    for i, item in enumerate(todo.itertuples(index=False), 1):
        try:
            sectors, top10 = fetch_one(sess, item.ISIN)
        except Exception:
            failed.append(item.ISIN)    # 실패는 기록하지 않는다 → 재실행 때 재시도
            time.sleep(PAUSE)
            continue
        if not sectors and not top10:
            # '자료 없음'도 파일에 남긴다 — '못 받음'과 구분되고, 재실행 때 안 두드린다
            empty_buf.append({"ISIN": item.ISIN, "종목명": item.종목명})
        for r in sectors:
            r["ISIN"] = item.ISIN
            sector_buf.append(r)
        for r in top10:
            r["ISIN"] = item.ISIN
            top10_buf.append(r)
        if i % 100 == 0:
            flush()                     # 100종목마다 저장 — 중간에 죽어도 안 잃는다
            print(f"  {i}/{len(todo)} — 실패 {len(failed)}", flush=True)
        time.sleep(PAUSE)
    flush()

    print(f"완료 — 요청 실패 {len(failed):,} (재실행하면 다시 시도한다)")
    if failed:
        print("실패 ISIN (앞 5개):", failed[:5])


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    collect(limit)
    sector_path, top10_path, empty_path = _paths()
    for p in (sector_path, top10_path, empty_path):
        if p.exists():
            print(f"저장: {p} ({len(pd.read_csv(p, dtype=str)):,}행)")
    if top10_path.exists():
        print("\n[TOP10 상위 5행]")
        print(pd.read_csv(top10_path, dtype=str).head(5).to_string(index=False))


if __name__ == "__main__":
    main()
