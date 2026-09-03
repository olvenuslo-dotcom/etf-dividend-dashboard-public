# -*- coding: utf-8 -*-
"""KRX PDF(Portfolio Deposit File) — ETF **전체 구성종목** 수집.

KRX 정보데이터시스템 [13108] 화면과 같은 값이다(pykrx `PDF` 클래스, 로그인 필요).
세이브로 보유자산명세(compst.py)는 상세가 **TOP10 뿐**이라 40단계에서 접었는데,
KRX 로그인이 뚫리면서(42단계) 전체 구성이 열렸다 — 실측 겹침이 v1(섹터 근사)
→ **v2(진짜 보유종목 min-합)** 로 올라가는 재료다.

- 종목당 1요청, 조회 일자는 KRX 정보 CSV 의 기준일(다른 데이터와 맞춘다)
- 중간에 죽어도 다시 실행하면 이어받는다 (같은 날짜 CSV 의 ISIN 은 건너뜀)
- KRX_ID/KRX_PW 로그인(.env) + 회사 PC 는 truststore

실행: venv\\Scripts\\python src\\krx_pdf.py        (전 종목, 15분 안팎)
      venv\\Scripts\\python src\\krx_pdf.py 5      (앞 5종목만 — 시험용)
"""
from pathlib import Path
import re
import sys
import time

import truststore
truststore.inject_into_ssl()
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

import env_keys                  # noqa: E402
import etf_info                  # noqa: E402

BASE = Path(__file__).resolve().parent.parent
RAW_DIR = BASE / "data" / "raw"

PAUSE = 0.4                      # 호출 간격(초) — 몰아치지 않는다

# 응답 태그 → 우리 컬럼. ISIN 은 모(母) ETF, 구성종목코드는 KRX 단축코드
COLS = {
    "COMPST_ISU_CD": "구성종목코드",
    "COMPST_ISU_CD2": "구성종목ISIN",
    "MKT_ID": "시장ID",
    "SECUGRP_ID": "증권구분",
    "COMPST_ISU_NM": "구성종목명",
    "COMPST_ISU_CU1_SHRS": "CU당수량",
    "VALU_AMT": "평가금액",
    "COMPST_AMT": "구성금액",
    "COMPST_RTO": "비중",
}


def base_date() -> str:
    """KRX 정보 CSV 파일명의 기준일 — 다른 국내 데이터와 같은 날짜로 조회한다."""
    path = etf_info.latest_info_csv()
    if path is None:
        raise FileNotFoundError("data/raw 에 krx_etf정보_*.csv 가 없습니다.")
    found = re.search(r"(\d{8})", path.name)
    if not found:
        raise ValueError(f"기준일을 파일명에서 못 읽었습니다: {path.name}")
    return found.group(1)


def _paths(stamp: str) -> tuple[Path, Path]:
    return (RAW_DIR / f"krx_PDF_{stamp}.csv",
            RAW_DIR / f"krx_PDF없음_{stamp}.csv")


def _done_isins(*paths: Path) -> set[str]:
    done: set[str] = set()
    for p in paths:
        if p.exists():
            done |= set(pd.read_csv(p, dtype=str)["ISIN"].dropna())
    return done


def _append(frame: pd.DataFrame, path: Path):
    if frame.empty:
        return
    frame.to_csv(path, mode="a", header=not path.exists(),
                 index=False, encoding="utf-8-sig")


def fetch_one(isin: str, stamp: str) -> pd.DataFrame:
    # ⚠ KDM 자동 조회 금지 — 이 스크립트가 8/14 에 IP 차단을 불렀다(44단계-취소).
    # 코드는 수동 다운로드 파싱에 재활용할 수 있어 남겨 두되, **돌지는 않게** 막는다.
    # 구성종목의 대체 경로는 `kis_top30.py`(한투 [0245], TOP30)다.
    import etf_info
    etf_info._guard_kdm()
    from pykrx.website.krx.etx.core import PDF
    raw = PDF().fetch(stamp, isin)
    if raw.empty:
        return raw
    keep = {tag: name for tag, name in COLS.items() if tag in raw.columns}
    out = raw[list(keep)].rename(columns=keep)
    out.insert(0, "ISIN", isin)
    out.insert(1, "기준일", stamp)
    # 수량·금액·비중의 콤마를 떼서 숫자로 (엑셀을 거치지 않아도 되게)
    for column in ("CU당수량", "평가금액", "구성금액", "비중"):
        if column in out.columns:
            out[column] = pd.to_numeric(
                out[column].astype(str).str.replace(",", "", regex=False),
                errors="coerce")
    return out


def collect(limit: int | None = None):
    env_keys.load()              # pykrx 로그인용 KRX_ID/KRX_PW
    stamp = base_date()
    info = etf_info.load_latest()
    frame = info.dropna(subset=["ISIN"]).drop_duplicates("ISIN")[["ISIN", "종목명"]]
    if limit:
        frame = frame.head(limit)
    pdf_path, empty_path = _paths(stamp)
    done = _done_isins(pdf_path, empty_path)
    todo = frame[~frame["ISIN"].isin(done)]
    print(f"기준일 {stamp} — 대상 {len(frame):,}종목 / 이미 받음 {len(done):,} / "
          f"이번에 {len(todo):,}종목")
    if todo.empty:
        return

    buffer: list[pd.DataFrame] = []
    empty_buf: list[dict] = []
    failed: list[str] = []

    def flush():
        if buffer:
            _append(pd.concat(buffer, ignore_index=True), pdf_path)
            buffer.clear()
        _append(pd.DataFrame(empty_buf), empty_path)
        empty_buf.clear()

    for i, item in enumerate(todo.itertuples(index=False), 1):
        try:
            got = fetch_one(item.ISIN, stamp)
        except Exception:
            failed.append(item.ISIN)    # 기록 안 함 → 재실행 때 재시도
            time.sleep(PAUSE)
            continue
        if got.empty:
            empty_buf.append({"ISIN": item.ISIN, "종목명": item.종목명})
        else:
            buffer.append(got)
        if i % 50 == 0:
            flush()                     # 50종목마다 저장 — 중간에 죽어도 안 잃는다
            print(f"  {i}/{len(todo)} — 실패 {len(failed)}", flush=True)
        time.sleep(PAUSE)
    flush()

    print(f"완료 — 요청 실패 {len(failed):,} (재실행하면 다시 시도한다)")
    if failed:
        print("실패 ISIN (앞 5개):", failed[:5])


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    collect(limit)
    stamp = base_date()
    for p in _paths(stamp):
        if p.exists():
            print(f"저장: {p} ({len(pd.read_csv(p, dtype=str)):,}행)")
    pdf_path = _paths(stamp)[0]
    if pdf_path.exists():
        frame = pd.read_csv(pdf_path, dtype=str)
        print("\n[상위 5행]")
        print(frame.head(5).to_string(index=False))


if __name__ == "__main__":
    main()
