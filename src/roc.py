# -*- coding: utf-8 -*-
"""미국 ETF **19a-1 Notice** — 분배금 중 원금 반환(ROC) 비중을 받아 온다.

왜 필요한가: 분배금이 커 보여도 그중 상당액이 **내가 넣은 원금을 돌려주는 것**이면
번 돈이 아니다. 미국 펀드는 그럴 때 19a-1 통지를 내야 하고, 거기에 이번 분배와
회계연도 누적의 **순투자소득 / 원금반환 비율**이 적혀 있다.

30단계의 분배변동계수는 이걸 못 구해서 쓴 **대용 신호**였다. 여기서는 진짜 값을 받는다.

### 어디서 받나
발행사마다 공시 자리가 달라서 **발행사별로** 붙인다. 지금은 Global X 만 넣었다.
- 종목 페이지 HTML 에 19a Notice 링크가 박혀 있다
- 그 링크가 `.docx` 라 **표를 구조 그대로** 읽을 수 있다(글자만 뽑으면 숫자 사이에
  공백이 섞여 `$0. 1 775` 처럼 나와 잘못 읽기 쉽다 — 그래서 표 칸으로 읽는다)

### 발행사마다 공시 자리가 다르다 (넷 다 `robots.txt` 확인 후 — 20단계 원칙)
| 발행사 | 어디서 | 무엇을 주나 |
|---|---|---|
| Global X | 종목 페이지의 `.docx` 19a Notice | 이번 회차 **+ 회계연도 누적** |
| Roundhill | 종목 페이지 **본문 문구** | 이번 회차만 |
| Rex | 종목 페이지 **본문 문구** (`Crawl-delay: 10` 지킴) | 이번 회차만 |
| GraniteShares | `/product/{id}/en-us/` JSON | 이번 회차만 |

**Global X 만 누적을 준다.** 나머지 셋은 '가장 최근 회차'뿐이라 `누적_원금반환비율`
칸이 빈다. 없는 값을 이번 회차로 채워 넣지 않는다 — 대신 `routine.py` 가
누적이 없으면 이번 회차로 판정하고 **그렇게 판정했다고 화면에 적는다.**

Rex 의 19a 링크는 **PDF** 로 간다. 본문에 같은 숫자가 문장으로 박혀 있어
PDF 를 열지 않고 그 문장을 읽는다.

### 안 받는 발행사
| 발행사 | 고배당(15%+) 종목 | robots |
|---|---|---|
| YieldMax | 37 | `/wp-content/uploads/docs/` **금지** — 공시 PDF 자리라 못 받는다 |
| Defiance | 12 | `/wp-content/uploads/funddocs/` **금지** |

실행: venv\\Scripts\\python src\\roc.py               (붙인 발행사 전부)
      venv\\Scripts\\python src\\roc.py Rex           (한 발행사만)
      venv\\Scripts\\python src\\roc.py QYLD XYLD     (Global X 몇 개만)
"""
from datetime import date
from pathlib import Path
import html
import io
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
import zipfile

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))

BASE = Path(__file__).resolve().parent.parent
RAW_DIR = BASE / "data" / "raw"
RAW_DIR.mkdir(parents=True, exist_ok=True)

# HTTP 헤더는 latin-1 로만 보낼 수 있다 — 한글을 넣으면 요청이 통째로 죽는다
UA = "etf-dividend-dashboard (personal research; contact olvenuslo@gmail.com)"
PAUSE = 0.6          # 발행사 사이트에 몰아치지 않는다

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

# Global X 종목 페이지와, 그 안에 박혀 있는 19a Notice 링크 모양
GLOBALX_FUND = "https://www.globalxetfs.com/funds/{ticker}/"
GLOBALX_19A = re.compile(
    r"https://assets\.globalxetfs\.com/funds/tax_supplements/"
    r"[A-Za-z0-9]+_Form-19a_\d+\.docx")

# 19a 표의 줄 이름 → 우리 칸
ROW_LABELS = {
    "net investment income": "순투자소득",
    "return of capital": "원금반환",
    "net realized short-term capital gains": "단기실현이익",
    "net realized long-term capital gains": "장기실현이익",
}

OUTPUT_COLUMNS = [
    "티커", "발행사", "공시일", "분배금",
    # 이번 회차
    "원금반환비율", "순투자소득비율",
    # 회계연도 누적 — 한 회차만 보면 그 달만 유난했을 수 있다
    "누적_원금반환비율", "누적_순투자소득비율",
    "출처",
]


def _number(text: str) -> float | None:
    """`$ 1. 5868` · `9 9.29 %` 처럼 공백이 섞여 와도 숫자로 읽는다."""
    cleaned = re.sub(r"[^\d.\-]", "", str(text))
    if not cleaned or cleaned in {"-", "."}:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def docx_tables(data: bytes) -> list[list[list[str]]]:
    """docx 의 표를 [표][행][칸] 로 돌려준다.

    글자만 뽑지 않고 **칸 단위**로 읽는 이유: 워드가 숫자를 여러 조각으로 쪼개 담아서
    이어붙이면 `$0. 1 775` 가 되고, 그걸 다시 자르다 `0.1` 로 잘못 읽기 쉽다.
    """
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
    tables = []
    for table in root.iter(f"{W}tbl"):
        rows = []
        for row in table.iter(f"{W}tr"):
            cells = []
            for cell in row.iter(f"{W}tc"):
                text = "".join(node.text or "" for node in cell.iter(f"{W}t"))
                cells.append(" ".join(text.split()))
            if cells:
                rows.append(cells)
        if rows:
            tables.append(rows)
    return tables


def parse_19a(data: bytes) -> dict:
    """19a Notice docx → {줄이름: {금액, 비율, 누적금액, 누적비율}}.

    표 한 줄이 [항목, 이번 금액, 이번 %, 누적 금액, 누적 %] 다.
    칸 수가 다른 양식이 오면 **읽은 만큼만** 담는다(엉뚱한 자리를 비율로 읽지 않게).
    """
    found: dict[str, dict] = {}
    for table in docx_tables(data):
        for row in table:
            if not row:
                continue
            label = ROW_LABELS.get(row[0].strip().lower().rstrip(":"))
            if label is None:
                continue
            values = [_number(cell) for cell in row[1:]]
            entry = {}
            for key, value in zip(("금액", "비율", "누적금액", "누적비율"), values):
                if value is not None:
                    entry[key] = value
            if entry:
                found[label] = entry
    return found


def fetch_globalx(ticker: str, session: requests.Session) -> dict | None:
    """Global X 한 종목의 ROC. 19a 공시가 없으면 None(= 공시 자체가 없다)."""
    page = session.get(GLOBALX_FUND.format(ticker=ticker.lower()), timeout=30)
    if page.status_code != 200:
        return None
    match = GLOBALX_19A.search(page.text)
    if not match:
        return None                    # ROC 가 없으면 19a 를 낼 일이 없다
    url = match.group(0)
    doc = session.get(url, timeout=30)
    if doc.status_code != 200:
        return None
    parsed = parse_19a(doc.content)
    roc = parsed.get("원금반환", {})
    income = parsed.get("순투자소득", {})
    stamp = re.search(r"_(\d{8})\.docx$", url)
    return {
        "티커": ticker.upper(),
        "발행사": "Global X Funds",
        # 파일명 날짜는 MMDDYYYY 다 (YYYYMMDD 아님)
        "공시일": (f"{stamp.group(1)[4:]}-{stamp.group(1)[:2]}-{stamp.group(1)[2:4]}"
                   if stamp else ""),
        "분배금": roc.get("금액"),
        "원금반환비율": roc.get("비율"),
        "순투자소득비율": income.get("비율"),
        "누적_원금반환비율": roc.get("누적비율"),
        "누적_순투자소득비율": income.get("누적비율"),
        "출처": url,
    }


# ── Roundhill ───────────────────────────────────────────
# 종목 페이지 본문에 이렇게 박혀 있다:
#   "Per the Fund's most recent 19a-1 notice, the estimated per share composition
#    of the distribution includes return of capital (ROC) of 100%."
# **위험고지 보일러플레이트와 반드시 갈라야 한다.** 모든 종목 페이지에 "distributions
# ... will be treated as a return of capital" 같은 일반 문구가 따로 있는데, 그건
# 공시값이 아니라 '그럴 수 있다'는 설명이다. 그래서 `(ROC) of 숫자%` 만 잡는다.
ROUNDHILL_FUND = "https://www.roundhillinvestments.com/etf/{ticker}/"
ROUNDHILL_ROC = re.compile(r"return of capital \(ROC\) of\s*([\d.]+)\s*%", re.I)

# ── Rex ─────────────────────────────────────────────────
# "Current distributions consist of 100% estimated return of capital (ROC)."
REX_FUND = "https://www.rexshares.com/{ticker}/"
REX_ROC = re.compile(
    r"consist of\s*([\d.]+)\s*%\s*estimated return of capital", re.I)

# ── GraniteShares ───────────────────────────────────────
# 종목 페이지가 id 로 JSON 을 부르고, fundYield 안에 ROC 가 있다. 화면의 ROC 자리는
# 이 JSON 으로 채우는 것이라 HTML 만 읽으면 `[show the ROC from the most recent
# 19a1 notice]%` 라는 **안 채워진 자리표시자**가 나온다 — 그래서 JSON 을 본다.
#
# ⚠ **페이지에 숫자 id 가 두 개 있다. 헷갈리면 남의 펀드 값을 가져온다.**
#   getProductId() → API 의 id     (TSYY = 1119)  ← 이것이 맞다
#   getId()        → CMS 노드 id   (TSYY = 1164)
# 처음에 getId() 를 썼다가 TSYY 자리에 ANV(전혀 다른 펀드)의 ROC 0.00 을 받아 왔다
# — 실제 TSYY 는 95.96 이다. 조용히 틀리는 종류의 사고라, 받아 온 JSON 의
# `Ticker` 가 요청한 티커와 **같은지 반드시 대조**한다.
GRANITE_FUND = "https://graniteshares.com/etfs/{ticker}/"
GRANITE_API = "https://graniteshares.com/product/{pid}/en-us/"
GRANITE_ID = re.compile(r"function\s+getProductId\(\)\s*\{\s*return\s*'(\d+)'")


def _text_of(response: requests.Response) -> str:
    """HTML 엔티티를 푼 본문 (`&#8217;` 같은 게 문장 중간에 섞여 있다)."""
    return html.unescape(response.text)


def _from_page(ticker: str, session: requests.Session, url: str,
               pattern: re.Pattern, issuer: str) -> dict | None:
    """종목 페이지 본문에서 이번 회차 ROC 만 읽는다 (누적은 안 준다)."""
    page = session.get(url.format(ticker=ticker.lower()), timeout=30)
    if page.status_code != 200:
        return None
    match = pattern.search(_text_of(page))
    if not match:
        return None            # ROC 문구가 없다 = 공시할 ROC 가 없다
    return {
        "티커": ticker.upper(), "발행사": issuer, "공시일": "", "분배금": None,
        "원금반환비율": float(match.group(1)),
        "순투자소득비율": None,
        # 이 발행사들은 회계연도 누적을 안 준다. 이번 회차로 채우지 않고 비운다.
        "누적_원금반환비율": None, "누적_순투자소득비율": None,
        "출처": page.url,
    }


def fetch_roundhill(ticker: str, session: requests.Session) -> dict | None:
    return _from_page(ticker, session, ROUNDHILL_FUND, ROUNDHILL_ROC,
                      "Roundhill Investments")


def fetch_rex(ticker: str, session: requests.Session) -> dict | None:
    return _from_page(ticker, session, REX_FUND, REX_ROC, "Rex")


def fetch_graniteshares(ticker: str, session: requests.Session) -> dict | None:
    """종목 페이지 → productId → JSON. ROC 가 없으면 None."""
    page = session.get(GRANITE_FUND.format(ticker=ticker.lower()), timeout=30)
    if page.status_code != 200:
        return None
    found = GRANITE_ID.search(page.text)
    if not found:
        return None
    api = session.get(GRANITE_API.format(pid=found.group(1)), timeout=30)
    if api.status_code != 200:
        return None
    payload = api.json()
    # ⚠ id 를 잘못 집으면 **다른 펀드 값이 조용히 들어온다.** 티커를 대조한다.
    if str(payload.get("Ticker") or "").strip().upper() != ticker.upper():
        return None
    raw = payload.get("fundYield") or ""
    if not raw:
        return None                      # 분배 자료 자체가 없는 상품
    entries = json.loads(raw)
    if not entries:
        return None
    entry = entries[0]
    roc = entry.get("ROC")
    if roc is None:
        return None
    return {
        "티커": ticker.upper(), "발행사": "GraniteShares",
        "공시일": str(entry.get("DistributionDate") or "")[:10],
        "분배금": None,
        "원금반환비율": float(roc),
        "순투자소득비율": None,
        "누적_원금반환비율": None, "누적_순투자소득비율": None,
        "출처": api.url,
    }


# 발행사별 수집 방법. 새 발행사를 붙일 때 여기에 한 줄 더한다.
#   운용사  = 우리 목록(`분배ETF목록.xlsx`)에 적힌 이름들. 한 발행사가 두 이름으로
#            들어와 있기도 하다 (Roundhill Financial / Roundhill Investments)
#   쉼      = 호출 간격(초). Rex 는 robots.txt 의 `Crawl-delay: 10` 을 지킨다
ISSUERS = {
    "Global X Funds": {
        "운용사": ["Global X Funds"], "fetch": fetch_globalx, "쉼": 0.6},
    "Roundhill": {
        "운용사": ["Roundhill Financial", "Roundhill Investments"],
        "fetch": fetch_roundhill, "쉼": 1.0},
    "Rex": {
        "운용사": ["Rex"], "fetch": fetch_rex, "쉼": 10.0},
    "GraniteShares": {
        "운용사": ["Graniteshares"], "fetch": fetch_graniteshares, "쉼": 1.0},
}

FETCHERS = {name: spec["fetch"] for name, spec in ISSUERS.items()}


def target_tickers(issuer: str = "Global X Funds") -> list[str]:
    """대시보드 목록에서 그 발행사의 미국 상장 종목 티커."""
    xlsx = BASE / "output" / "분배ETF목록.xlsx"
    if not xlsx.exists():
        return []
    names = ISSUERS.get(issuer, {}).get("운용사", [issuer])
    frame = pd.read_excel(xlsx, sheet_name="전체")
    hit = frame[(frame["상장시장"] == "미국")
                & (frame["운용사"].astype(str).str.strip().isin(names))]
    return sorted(hit["종목코드"].dropna().astype(str).str.strip().unique())


def collect(tickers: list[str] | None = None,
            issuer: str = "Global X Funds") -> pd.DataFrame:
    spec = ISSUERS[issuer]
    fetch, pause = spec["fetch"], spec["쉼"]
    tickers = tickers or target_tickers(issuer)
    print(f"{issuer} — 대상 {len(tickers)}종목 (호출 간격 {pause}초)")

    session = requests.Session()
    session.headers.update({"User-Agent": UA})
    rows, no_notice, failed = [], [], []
    for i, ticker in enumerate(tickers, 1):
        try:
            got = fetch(ticker, session)
        except Exception:
            failed.append(ticker)
            got = None
        if got:
            rows.append(got)
        elif ticker not in failed:
            no_notice.append(ticker)
        print(f"  {i}/{len(tickers)} — 공시 {len(rows)}건", end="\r")
        time.sleep(pause)

    print(f"\n{issuer}: 공시 있음 {len(rows)}종목 / 없음 {len(no_notice)}종목 "
          f"/ 실패 {len(failed)}종목")
    if failed:
        print(f"  실패: {', '.join(failed[:8])}")
    return pd.DataFrame(rows, columns=OUTPUT_COLUMNS)


def latest_csv() -> Path | None:
    files = sorted(RAW_DIR.glob("us_roc_*.csv"))
    return files[-1] if files else None


def load_latest() -> pd.DataFrame:
    """저장된 ROC 표. 없으면 빈 표(대시보드는 이 칸 없이 그대로 돈다)."""
    path = latest_csv()
    if path is None:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    frame = pd.read_csv(path, dtype={"티커": str})
    for column in ("분배금", "원금반환비율", "순투자소득비율",
                   "누적_원금반환비율", "누적_순투자소득비율"):
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


def main():
    """인자 없으면 붙인 발행사 전부. 발행사 이름 하나면 그것만. 티커면 Global X."""
    args = sys.argv[1:]
    if len(args) == 1 and args[0] in ISSUERS:
        frames = [collect(None, args[0])]
    elif args:
        frames = [collect([t.upper() for t in args], "Global X Funds")]
    else:
        frames = [collect(None, name) for name in ISSUERS]

    frame = pd.concat([f for f in frames if not f.empty], ignore_index=True) \
        if any(not f.empty for f in frames) else pd.DataFrame(columns=OUTPUT_COLUMNS)
    if frame.empty:
        print("받은 공시가 없습니다.")
        return 1

    # 발행사 하나만 돌렸을 때 **나머지 발행사 수집분이 날아가지 않게** 합친다.
    # (앞의 것을 남기고 이번에 받은 것으로 덮는다 — 새 값이 최신이다)
    previous = load_latest()
    if not previous.empty:
        frame = pd.concat([frame, previous], ignore_index=True)
    frame = frame.drop_duplicates("티커", keep="first")

    out = RAW_DIR / f"us_roc_{date.today():%Y%m%d}.csv"
    frame.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n저장: {out} ({len(frame)}종목)")
    print(frame["발행사"].value_counts().to_string())

    # 누적을 주는 곳은 Global X 뿐이라, 없으면 이번 회차로 세워 보여 준다
    frame["_판정값"] = frame["누적_원금반환비율"].fillna(frame["원금반환비율"])
    print("\n[원금반환 비율 높은 순 — 누적이 없으면 최근 회차]")
    top = frame.nlargest(12, "_판정값")
    print(top[["티커", "발행사", "원금반환비율", "누적_원금반환비율"]]
          .to_string(index=False))
    return 0


if __name__ == "__main__":
    import corp_ca
    from console import use_utf8_stdout

    corp_ca.apply()
    use_utf8_stdout()
    sys.exit(main())
