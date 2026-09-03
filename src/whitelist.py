"""회사 화이트리스트 — 임직원 계좌로 매매 가능한 종목만 걸러낸다.

파일: data/reference/whitelist.csv
컬럼: 종목코드,구분,종목명,상태

- 종목코드는 앞에 'A'가 붙어 있어도 되고 없어도 된다. 붙어 있으면 떼고 6자리로 맞춘다
- 매칭은 **종목코드 우선**, 코드가 없을 때만 종목명으로 맞춘다
  (종목명은 운용사가 바꾸는 일이 있어서 코드가 더 안전하다)
- 파일이 없으면 빈 목록을 돌려준다. 대시보드는 필터를 그냥 숨긴다
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parent.parent
WHITELIST_PATH = BASE / "data" / "reference" / "whitelist.csv"

COLUMNS = ["종목코드", "구분", "종목명", "상태"]


def normalize_code(value) -> str:
    """'A252670' · 252670 · '252670.0' 을 모두 '252670' 으로 맞춘다."""
    if pd.isna(value):
        return ""
    text = str(value).strip().upper()
    if text.endswith(".0"):          # 엑셀에서 숫자로 읽히면 이렇게 들어온다
        text = text[:-2]
    if text.startswith("A"):
        text = text[1:]
    text = "".join(ch for ch in text if ch.isalnum())
    return text.zfill(6) if text.isdigit() else text


def load(path: Path | None = None) -> pd.DataFrame:
    """화이트리스트를 읽는다. 없으면 빈 표를 돌려준다."""
    path = path or WHITELIST_PATH
    if not path.exists():
        return pd.DataFrame(columns=COLUMNS)
    try:
        wl = pd.read_csv(path, dtype=str).fillna("")
    except Exception:
        return pd.DataFrame(columns=COLUMNS)

    for column in COLUMNS:
        if column not in wl.columns:
            wl[column] = ""
    wl["코드"] = wl["종목코드"].map(normalize_code)
    wl["이름"] = wl["종목명"].astype(str).str.strip()
    # 상태가 '삭제'인 행은 이제 못 사는 종목이라 뺀다
    wl = wl[~wl["상태"].astype(str).str.strip().isin(["삭제", "제외"])]
    return wl[wl["코드"].ne("") | wl["이름"].ne("")].reset_index(drop=True)


def mark(df: pd.DataFrame, wl: pd.DataFrame | None = None) -> pd.DataFrame:
    """ETF 표에 '화이트리스트' 불리언 칸을 붙여서 돌려준다."""
    wl = load() if wl is None else wl
    out = df.copy()
    if wl.empty:
        out["화이트리스트"] = False
        return out

    codes = set(wl["코드"]) - {""}
    names = set(wl["이름"]) - {""}

    df_code = df["KRX코드"].map(normalize_code) if "KRX코드" in df.columns \
        else pd.Series("", index=df.index)
    df_name = df["종목명"].astype(str).str.strip() if "종목명" in df.columns \
        else pd.Series("", index=df.index)

    # 코드가 맞으면 그걸 믿고, 코드가 비었을 때만 이름으로 본다
    hit_code = df_code.isin(codes) & df_code.ne("")
    hit_name = df_name.isin(names) & df_code.eq("")
    out["화이트리스트"] = hit_code | hit_name
    return out


def unmatched(df: pd.DataFrame, wl: pd.DataFrame | None = None) -> list[str]:
    """화이트리스트에는 있는데 ETF 목록에서 못 찾은 종목명. 오타·상장폐지 확인용."""
    wl = load() if wl is None else wl
    if wl.empty:
        return []
    have_codes = set(df["KRX코드"].map(normalize_code)) if "KRX코드" in df.columns else set()
    have_names = set(df["종목명"].astype(str).str.strip()) if "종목명" in df.columns else set()
    missing = []
    for _, row in wl.iterrows():
        if row["코드"] and row["코드"] in have_codes:
            continue
        if row["이름"] and row["이름"] in have_names:
            continue
        missing.append(row["이름"] or row["코드"])
    return missing
