# -*- coding: utf-8 -*-
"""한국은행 ECOS 경제통계 수집 — 시장국면 대시보드용.

인증키는 `.env` 의 `ECOS_API_KEY` 에서 읽는다(https://ecos.bok.or.kr/api/ 무료 발급).

받아 둔 구간은 `data/ecos/` 에 지표별 CSV 로 쌓는다. 다음에 돌리면 **부족한 구간만**
채운다 — 처음엔 1년, 다음엔 3년… 이렇게 기간을 늘려 가며 이력을 불려도 된다.

실행: venv\\Scripts\\python src\\ecos.py           (기본 1년)
      venv\\Scripts\\python src\\ecos.py 3y        (3년치까지 받아 이력에 더함)
"""
from pathlib import Path
import os
import sys

import pandas as pd

BASE = Path(__file__).resolve().parent.parent
DATA_DIR = BASE / "data" / "ecos"

API = "https://ecos.bok.or.kr/api/StatisticSearch"
MAX_ROWS = 10000        # ECOS 한 번에 받을 수 있는 최대 건수

# 지표 정의 — (통계표코드, 항목코드, 주기)
#   D=일별 M=월별. 주기가 다르면 날짜 형식도 다르다(YYYYMMDD vs YYYYMM).
SERIES = {
    "국고채10년": {
        "stat": "817Y002", "item": "010210000", "cycle": "D",
        "label": "국고채 10년 금리", "unit": "%",
        # 2000-12-18 부터 있다
    },
    "수출금액": {
        "stat": "901Y118", "item": "T002", "cycle": "M",
        "label": "통관 수출금액", "unit": "천달러",
    },
    "투자자예탁금": {
        "stat": "901Y056", "item": "S23A", "cycle": "M",
        # 값이 원 단위로 온다 (2026-07 = 104,135,408,405,298 → 104조원)
        "label": "투자자 예탁금", "unit": "원",
        # 1998-06 부터 있다 — '역사적 평균 대비 지금 어디쯤인가'를 볼 수 있다.
        # 월별이다. 일별이 필요해지면 금융투자협회(FreeSIS)를 따로 붙여야 한다.
    },
    "DRAM수출물가": {
        "stat": "402Y016", "item": "30911201AA", "cycle": "M",
        # ⚠ 이 표는 한 달에 세 줄이 온다 — 계약통화(C)·달러(D)·원화(W) 기준.
        #    안 고르면 중복 제거 때 아무거나 남는다. DRAM 은 달러로 거래되고
        #    TrendForce 현물가도 달러라 **달러기준(D)** 으로 맞춘다.
        "item2": "D",
        "label": "DRAM 수출물가지수(달러기준)", "unit": "2020=100",
        # TrendForce 현물가는 과거를 못 사서, 과거 구간은 이 지표로 본다
    },
}


def _key() -> str:
    """인증키. 없으면 무엇을 해야 하는지 알려 주고 멈춘다."""
    sys.path.insert(0, str(BASE / "src"))
    import env_keys

    env_keys.load()
    key = os.environ.get("ECOS_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "ECOS_API_KEY 가 없습니다. https://ecos.bok.or.kr/api/ 에서 무료 발급받아 "
            "저장소 루트 .env 에 ECOS_API_KEY=... 로 넣어 주세요.")
    return key


def csv_path(name: str) -> Path:
    return DATA_DIR / f"{name}.csv"


def _period(stamp: pd.Timestamp, cycle: str) -> str:
    """주기에 맞는 날짜 문자열. 일별은 YYYYMMDD, 월별은 YYYYMM."""
    return stamp.strftime("%Y%m%d" if cycle == "D" else "%Y%m")


def fetch(name: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """한 지표를 구간만큼 받아 (날짜, 값) 표로 돌려준다."""
    import requests

    spec = SERIES[name]
    parts = [
        API, _key(), "json", "kr", "1", str(MAX_ROWS),
        spec["stat"], spec["cycle"],
        _period(start, spec["cycle"]), _period(end, spec["cycle"]),
        spec["item"],
    ]
    if spec.get("item2"):      # 통화기준처럼 항목이 2단인 표
        parts.append(spec["item2"])
    payload = requests.get("/".join(parts), timeout=60).json()

    if "RESULT" in payload:      # ECOS 는 오류도 200 으로 준다
        result = payload["RESULT"]
        raise RuntimeError(f"ECOS 오류 {result.get('CODE')}: {result.get('MESSAGE')}")

    rows = payload.get("StatisticSearch", {}).get("row", [])
    if not rows:
        return pd.DataFrame(columns=["날짜", "값"])

    frame = pd.DataFrame({
        "날짜": [r["TIME"] for r in rows],
        "값": pd.to_numeric([r["DATA_VALUE"] for r in rows], errors="coerce"),
    })
    return frame.dropna(subset=["값"]).reset_index(drop=True)


def load(name: str) -> pd.DataFrame:
    """쌓아 둔 이력. 없으면 빈 표."""
    path = csv_path(name)
    if not path.exists():
        return pd.DataFrame(columns=["날짜", "값"])
    frame = pd.read_csv(path, dtype={"날짜": str})
    frame["값"] = pd.to_numeric(frame["값"], errors="coerce")
    return frame.dropna(subset=["값"])


def save(name: str, frame: pd.DataFrame) -> Path:
    """이력에 더한다. 같은 날짜는 새 값으로 바꾼다(수정 발표 반영)."""
    history = load(name)
    merged = (pd.concat([history, frame], ignore_index=True)
              if not history.empty else frame.copy())
    merged = (merged.drop_duplicates("날짜", keep="last")
              .sort_values("날짜").reset_index(drop=True))
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = csv_path(name)
    merged.to_csv(path, index=False, encoding="utf-8-sig")
    return path


def series(name: str) -> pd.DataFrame:
    """차트용 (날짜=datetime, 값). 일별·월별 모두 datetime 으로 맞춘다."""
    frame = load(name)
    if frame.empty:
        return pd.DataFrame(columns=["날짜", "값"])
    cycle = SERIES[name]["cycle"]
    fmt = "%Y%m%d" if cycle == "D" else "%Y%m"
    frame = frame.copy()
    frame["날짜"] = pd.to_datetime(frame["날짜"], format=fmt, errors="coerce")
    return frame.dropna(subset=["날짜"]).sort_values("날짜").reset_index(drop=True)


def update(years: float = 1.0, names: list[str] | None = None) -> dict[str, int]:
    """지정한 기간만큼 받아 이력에 더한다. 돌려주는 값은 {지표: 총 행수}."""
    end = pd.Timestamp.today()
    start = end - pd.DateOffset(years=int(years)) if years >= 1 else \
        end - pd.Timedelta(days=int(years * 365))

    out = {}
    for name in (names or SERIES):
        try:
            fresh = fetch(name, start, end)
        except Exception as exc:
            print(f"  {name}: 실패 ({type(exc).__name__}: {str(exc)[:90]})")
            continue
        if fresh.empty:
            print(f"  {name}: 받은 게 없습니다")
            continue
        save(name, fresh)
        total = len(load(name))
        out[name] = total
        print(f"  {name}: 새로 {len(fresh):,}건 → 이력 {total:,}건 "
              f"({load(name)['날짜'].min()}~{load(name)['날짜'].max()})")
    return out


def main() -> int:
    # 'python src/ecos.py 3y' 처럼 기간을 줄 수 있다
    arg = sys.argv[1] if len(sys.argv) > 1 else "1y"
    years = float(str(arg).lower().rstrip("y") or 1)

    print(f"ECOS 수집 — 최근 {years:g}년")
    result = update(years)
    if not result:
        print("받은 지표가 없습니다.")
        return 1
    print(f"\n저장 위치: {DATA_DIR}")
    return 0


if __name__ == "__main__":
    from console import use_utf8_stdout   # 콘솔이 cp949여도 안 죽게
    use_utf8_stdout()
    from corp_ca import apply as apply_corp_ca   # 회사 HTTPS 가로채기 뒤에서도 받게
    apply_corp_ca()
    sys.exit(main())
