# -*- coding: utf-8 -*-
"""KIS 구성종목 TOP30 수집 — 겹침 계산의 '신선한' 층.

8/13 KRX PDF 스냅샷(전체 구성)이 겹침의 기준이지만, KDM 자동 수집이 막혀
(44단계-취소) **갱신을 못 한다.** 한투 [0245] API 는 상위 30종목까지만 주지만
(45단계-보완 실측: KODEX 200 기준 비중합 84.5%) 언제든 다시 받을 수 있다.
겹침 사다리에서 PDF 다음, 섹터 앞에 선다: PDF → **KIS30** → 섹터 → 분류.

- 대상: 분배ETF목록의 한국 상장 전 종목 (미분배 포함 — 겹침은 미분배도 본다)
- `kis_api.component_stocks` 의 하루 캐시 덕에 중간에 죽어도 다시 돌리면
  이미 받은 종목은 API 를 안 부른다
- 채권형·해외주식형은 0행이 정상이다(API 가 국내 주식형만 준다) — 그 종목은
  자연히 아래 층(섹터·분류)으로 내려간다

실행: venv\\Scripts\\python src\\kis_top30.py        (전 종목, 10분 안팎)
      venv\\Scripts\\python src\\kis_top30.py 20     (앞 20종목 — 시험용)
"""
from datetime import date
from pathlib import Path
import sys
import time

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

import kis_api                   # noqa: E402

BASE = Path(__file__).resolve().parent.parent
RAW_DIR = BASE / "data" / "raw"


def target_codes() -> pd.DataFrame:
    """(ISIN, 종목코드, 종목명) — 한국 상장 ETF 전부.

    종목코드가 빈 종목(만기·상폐 등 234개)은 **ISIN 가운데 6자리**로 채운다 —
    국내 ISIN(KR7XXXXXX00C)의 4~9번째가 곧 KRX 단축코드다.
    """
    frame = pd.read_excel(BASE / "output" / "분배ETF목록.xlsx", sheet_name="전체")
    kr = frame[frame["상장시장"] == "한국"].dropna(subset=["ISIN"])
    kr = kr.drop_duplicates("ISIN").copy()
    isins = kr["ISIN"].astype(str).str.strip()
    codes = kr["종목코드"].astype(str).str.strip()
    codes = codes.where(codes.str.fullmatch(r"[A-Z0-9]{6}"), isins.str[3:9])
    keep = codes.str.fullmatch(r"[A-Z0-9]{6}")
    return pd.DataFrame({"ISIN": isins[keep], "종목코드": codes[keep],
                         "종목명": kr["종목명"].astype(str)[keep]})


def collect(limit: int | None = None) -> pd.DataFrame:
    targets = target_codes()
    if limit:
        targets = targets.head(limit)
    print(f"대상 {len(targets):,}종목 (한국 상장)")

    rows: list[pd.DataFrame] = []
    empty = failed = 0
    for i, item in enumerate(targets.itertuples(index=False), 1):
        try:
            got = kis_api.component_stocks(item.종목코드)
        except Exception:
            failed += 1                     # 다음 실행 때 캐시가 없어 재시도된다
            time.sleep(kis_api.PAUSE)
            continue
        if got.empty:
            empty += 1                      # 채권·해외형 — 정상이다
        else:
            got = got.copy()
            got.insert(0, "ISIN", item.ISIN)
            rows.append(got)
        if i % 100 == 0:
            print(f"  {i}/{len(targets)} — 자료 {len(rows)} / 빈 종목 {empty} "
                  f"/ 실패 {failed}", flush=True)
        time.sleep(kis_api.PAUSE)

    print(f"수집 끝 — 자료 {len(rows)}종목 / 빈 종목(채권·해외형) {empty} "
          f"/ 실패 {failed}")
    if not rows:
        return pd.DataFrame()
    return pd.concat(rows, ignore_index=True)


def main() -> int:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    frame = collect(limit)
    if frame.empty:
        print("받은 구성종목이 없습니다.")
        return 1
    out = RAW_DIR / f"kis_TOP30_{date.today():%Y%m%d}.csv"
    frame.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"저장: {out} ({frame['ISIN'].nunique():,}종목 {len(frame):,}행)")
    return 0


if __name__ == "__main__":
    import corp_ca
    from console import use_utf8_stdout

    corp_ca.apply()
    use_utf8_stdout()
    sys.exit(main())
