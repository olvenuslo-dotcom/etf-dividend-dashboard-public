# -*- coding: utf-8 -*-
"""ETF 배수(레버리지·인버스) 수집 — `etf_trc_ert_mltp` 를 종목마다 받는다.

**왜 따로 받아야 하나**: 6단계에서 확인했듯 KRX 가 주는 자산분류·운용방식·
기초지수명으로는 레버리지를 구분할 수 없다 — `KODEX 레버리지` 와 `KODEX 200` 이
전 칸 동일하고 기초지수도 똑같이 '코스피 200' 이다. 이름으로 추정하는 것은
CLAUDE.md 가 금지한다. 한투 [0245] ETF 현재가의 **추적수익률 배수**가 유일한
데이터 근거다 (실측: 레버리지 2.00 / 인버스 -1.00 / 일반 1.00).

- 국내 상장 ETF 전체가 대상. 종목당 1요청, 하루 캐시라 재실행이 싸다
- 미국 상장분은 이 API 대상이 아니다 → `us_etf` 의 `카테고리`(Leveraged/
  Inverse)로 방향만 알 수 있고 **배수는 모른다**. 빈칸으로 둔다

실행: venv\\Scripts\\python src\\kis_multiple.py        (전 종목, 10분 안팎)
      venv\\Scripts\\python src\\kis_multiple.py 20     (앞 20종목 — 시험용)
"""
from datetime import date
from pathlib import Path
import sys
import time

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

import kis_api                   # noqa: E402
import kis_top30                 # noqa: E402 - 대상 목록을 그대로 쓴다

BASE = Path(__file__).resolve().parent.parent
RAW_DIR = BASE / "data" / "raw"


def latest_csv() -> Path | None:
    files = sorted(RAW_DIR.glob("kis_배수_*.csv"))
    return files[-1] if files else None


def load_latest() -> pd.DataFrame:
    """저장된 배수 표. 없으면 빈 표(대시보드는 이 칸 없이 그대로 돈다).

    **0 은 '0배'가 아니라 미제공이라 여기서도 지운다.** 수집 시점에도 거르지만,
    그 처리가 들어가기 전에 저장된 CSV 가 이미 있어서(2026-08-15 수집분 31종목)
    읽는 쪽에서도 막는다. 0 을 그대로 쓰면 `SOL 미국테크TOP10인버스(합성)` 같은
    인버스가 '0배'로 표시된다.
    """
    path = latest_csv()
    if path is None:
        return pd.DataFrame(columns=["종목코드", "배수", "대표지수"])
    frame = pd.read_csv(path, dtype={"종목코드": str})
    values = pd.to_numeric(frame.get("배수"), errors="coerce")
    frame["배수"] = values.where(values != 0)
    return frame


def collect(limit: int | None = None) -> pd.DataFrame:
    targets = kis_top30.target_codes()
    if limit:
        targets = targets.head(limit)
    print(f"대상 {len(targets):,}종목 (한국 상장)")

    rows, failed = [], 0
    for i, item in enumerate(targets.itertuples(index=False), 1):
        try:
            got = kis_api.etf_price_info(item.종목코드)
        except Exception:
            failed += 1                 # 캐시가 없어 다음 실행 때 재시도된다
            time.sleep(kis_api.PAUSE)
            continue
        if got:
            got["ISIN"] = item.ISIN
            got["종목명"] = item.종목명
            rows.append(got)
        if i % 100 == 0:
            print(f"  {i}/{len(targets)} — 받음 {len(rows)} / 실패 {failed}",
                  flush=True)
        time.sleep(kis_api.PAUSE)

    print(f"수집 끝 — 받음 {len(rows)}종목 / 실패 {failed}")
    return pd.DataFrame(rows)


def main() -> int:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    frame = collect(limit)
    if frame.empty:
        print("받은 배수가 없습니다.")
        return 1
    out = RAW_DIR / f"kis_배수_{date.today():%Y%m%d}.csv"
    frame.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"저장: {out} ({len(frame):,}종목)")
    counts = frame["배수"].value_counts().sort_index()
    print("\n[배수 분포]")
    print(counts.to_string())
    return 0


if __name__ == "__main__":
    import corp_ca
    from console import use_utf8_stdout

    corp_ca.apply()
    use_utf8_stdout()
    sys.exit(main())
