# -*- coding: utf-8 -*-
"""DDR5 현물가 수집 — TrendForce 공개 표.

시장국면 대시보드에서 **국고채 10년 금리와 겹쳐 보려고** 쌓는다.

⚠ **과거 시계열은 살 수 없다.** TrendForce 는 오늘 값만 무료로 보여 주고
   History 는 유료(Gold+) 다. 그래서 **오늘부터 하루씩 쌓는 것 말고 방법이 없다.**
   과거 구간까지 보려면 ECOS 반도체 수출물가지수 같은 대용치를 함께 그려야 한다.

   robots.txt 는 /price/ 를 막지 않는다(막는 것은 /search 등). 하루 한 번만 받는다.

품목은 전부 저장하고 화면에서 고른다 — 어차피 한 페이지에 다 들어 있어
수집 비용이 같다. 대표 지표는 `MAIN_ITEM`(범용 칩 단가)이다.

실행: venv\\Scripts\\python src\\ddr5.py
"""
from datetime import date
from pathlib import Path
import sys

import pandas as pd

BASE = Path(__file__).resolve().parent.parent
HISTORY_CSV = BASE / "data" / "reference" / "ddr5_현물가.csv"

SPOT_URL = "https://www.trendforce.com/price/dram/dram_spot"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"

# 대표 지표 — 범용 DRAM 칩. 모듈(UDIMM·RDIMM)은 조립 마진·유통이 섞여 있어
# 금리·수출과의 동행을 볼 목적에는 칩 단가가 깨끗하다.
MAIN_ITEM = "DDR5 16Gb (2Gx8) 4800/5600"

OUTPUT_COLUMNS = ["날짜", "품목", "평균가", "고가", "저가"]


def _price(text: str) -> float | None:
    """'1,750.00' → 1750.0 / '▲2.68 %' 같은 건 숫자가 아니라 None."""
    cleaned = str(text).strip().replace(",", "")
    if not cleaned or cleaned in ("-", "N/A"):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def fetch(today: date | None = None) -> pd.DataFrame:
    """오늘자 DDR5 현물가. (날짜, 품목, 평균가, 고가, 저가)"""
    import requests
    from bs4 import BeautifulSoup

    today = today or date.today()
    html = requests.get(SPOT_URL, headers={"User-Agent": UA}, timeout=60).text
    return parse(html, today)


def parse(html: str, today: date) -> pd.DataFrame:
    """받아 온 HTML 에서 DDR5 줄만 골라낸다 (표 구조가 표마다 조금씩 다르다)."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for table in soup.find_all("table"):
        header = [th.get_text(strip=True) for th in table.find_all("th")]
        for tr in table.find_all("tr"):
            cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
            if not cells or "DDR5" not in cells[0]:
                continue
            item = cells[0]
            # 'Session Average' 칸 위치가 표마다 달라 머리글로 찾는다
            values = dict(zip(header, cells))
            avg = _price(values.get("Session Average"))
            if avg is None:      # 머리글을 못 맞추면 숫자 중 가장 그럴듯한 것
                numbers = [_price(c) for c in cells[1:]]
                numbers = [n for n in numbers if n is not None]
                avg = numbers[-1] if numbers else None
            rows.append({
                "날짜": today.isoformat(),
                "품목": item,
                "평균가": avg,
                "고가": _price(values.get("Session High") or values.get("Daily High")),
                "저가": _price(values.get("Session Low") or values.get("Daily Low")),
            })
    frame = pd.DataFrame(rows, columns=OUTPUT_COLUMNS)
    # 값이 없는 줄은 버린다. 머리글 없는 표(품목 이름만 나열된 칸)가 섞여 들어온다.
    frame = frame[frame["평균가"].notna()]
    # 같은 품목이 여러 표에 나오면(칩/모듈) 첫 번째만 둔다
    return frame.drop_duplicates("품목", keep="first").reset_index(drop=True)


def load_history() -> pd.DataFrame:
    """지금까지 쌓아 둔 시계열. 없으면 빈 표."""
    if not HISTORY_CSV.exists():
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    frame = pd.read_csv(HISTORY_CSV, dtype={"날짜": str, "품목": str})
    for column in ("평균가", "고가", "저가"):
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


def append(today_frame: pd.DataFrame) -> Path:
    """오늘치를 이력에 더한다. 같은 날짜·품목은 새 값으로 바꾼다(중복 방지)."""
    history = load_history()
    # 빈 표를 concat 하면 자료형 경고가 난다 → 있을 때만 붙인다
    merged = (pd.concat([history, today_frame], ignore_index=True)
              if not history.empty else today_frame.copy())
    merged = merged.drop_duplicates(["날짜", "품목"], keep="last")
    merged = merged.sort_values(["날짜", "품목"]).reset_index(drop=True)
    HISTORY_CSV.parent.mkdir(parents=True, exist_ok=True)
    merged.to_csv(HISTORY_CSV, index=False, encoding="utf-8-sig")
    return HISTORY_CSV


def series(item: str = MAIN_ITEM) -> pd.DataFrame:
    """한 품목의 시계열 (날짜, 평균가). 차트용."""
    history = load_history()
    if history.empty:
        return pd.DataFrame(columns=["날짜", "평균가"])
    picked = history[history["품목"] == item].copy()
    picked["날짜"] = pd.to_datetime(picked["날짜"], errors="coerce")
    return picked.dropna(subset=["날짜"]).sort_values("날짜")[["날짜", "평균가"]]


def main() -> int:
    # 스케줄러가 로그에 이어 붙이므로, 언제 돈 것인지 매번 남긴다
    stamp = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n===== {stamp} DDR5 수집 =====")

    try:
        today = fetch()
    except Exception as exc:
        # 스케줄러가 부르는 자리라 죽어도 아무도 못 본다 → 사유를 로그에 남긴다
        print(f"조회 실패: {type(exc).__name__}: {str(exc)[:200]}")
        return 1

    if today.empty:
        print("받은 게 없습니다 — 페이지 구조가 바뀌었을 수 있습니다.")
        return 1

    path = append(today)
    history = load_history()
    print(f"오늘자 {len(today)}개 품목 저장 → {path}")
    print(f"쌓인 이력: {history['날짜'].nunique()}일 / {len(history):,}행")
    print(f"\n[대표 지표] {MAIN_ITEM}")
    main_row = today[today["품목"] == MAIN_ITEM]
    print(f"  평균가 {main_row['평균가'].iloc[0]}" if not main_row.empty
          else "  (오늘 표에 없습니다)")
    print("\n[오늘 받은 품목]")
    print(today.to_string(index=False))
    return 0


if __name__ == "__main__":
    from console import use_utf8_stdout   # 콘솔이 cp949여도 안 죽게
    use_utf8_stdout()
    from corp_ca import apply as apply_corp_ca   # 회사 HTTPS 가로채기 뒤에서도 받게
    apply_corp_ca()
    sys.exit(main())
