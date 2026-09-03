# -*- coding: utf-8 -*-
"""
1단계-보조: ETF를 투자지역 / 테마·섹터 / 전략으로 나눈다

⚠ 이 모듈만 **추정**이다. 나머지 분류(자산분류·시장분류·운용방식·과세유형)는
   KRX가 값으로 주지만, 세부 지역(미국/중국)과 섹터(반도체/2차전지)는 KRX에 필드가 없다.

- 판단 근거는 **기초지수명(ETF_OBJ_IDX_NM)**. 종목명(마케팅용 이름)은 쓰지 않는다.
  예) `NYSE Semicondoctor Index` → 미국 / 반도체
- 규칙에 안 걸리면 '기타'. 억지로 끼워 맞추지 않는다
- 지역은 KRX 시장분류(국내/해외/국내&해외)를 **먼저 존중**한다.
  시장분류가 '국내'인데 지수명에 'US'가 있으면(예: 미국채 혼합) 시장분류를 따른다
"""
import re

import pandas as pd

# 영문 약어는 한글 바로 뒤에서도 잡히게 (\b 는 '온디바이스AI' 에서 안 걸린다)
def _abbr(word: str) -> str:
    return rf"(?<![A-Za-z]){word}(?![A-Za-z])"


# ── 투자지역: (라벨, 정규식) — 위에서부터 먼저 걸리는 것 ────────────
REGION_RULES = [
    ("미국", r"미국|United States|" + _abbr(r"U\.?S\.?A?") + r"|NASDAQ|나스닥|S&P\s*500|"
             r"S&P500|NYSE|Dow Jones|다우|러셀|Russell|필라델피아|PHLX|Treasury|TLT|SOFR|"
             r"엔비디아|NVIDIA|테슬라|Tesla|애플|Apple|아마존|Amazon|팔란티어|Palantir|"
             r"마이크로소프트|Microsoft|구글|Google|메타|버크셔|Berkshire|Dividend Monarchs"),
    ("중국", r"중국|China|Chinese|CSI|항셍|Hang\s*Seng|심천|Shenzhen|상해|Shanghai"),
    ("일본", r"일본|Japan|닛케이|Nikkei|TOPIX|엔화|Samurai"),
    ("인도", r"인도|India|Nifty|Sensex"),
    ("대만", r"대만|Taiwan|TSMC|TWSE"),
    ("베트남", r"베트남|Vietnam|VN30"),
    ("유럽", r"유럽|Europe|European|EuroStoxx|Euro Stoxx|STOXX|DAX|독일|Germany|영국|"
             + _abbr("UK")),
    ("신흥국", r"신흥국|Emerging|" + _abbr("EM") + r"|프론티어|Frontier"),
    ("글로벌", r"글로벌|Global|World|선진국|Developed|International|ACWI|GSCI"),
]

# ── 테마·섹터 ─────────────────────────────────────────────
SECTOR_RULES = [
    # 개별 기업 하나(또는 소수)를 따라가는 지수는 섹터로 묶기보다 따로 본다
    ("개별종목", r"삼성전자|SK하이닉스|현대차|엔비디아|NVIDIA|테슬라|Tesla|애플|Apple|"
                r"아마존|Amazon|팔란티어|Palantir|마이크로소프트|Microsoft|"
                r"버크셔|Berkshire|TSMC|카카오|네이버|고정테크"),
    ("반도체", r"반도체|Semicon|Semicondoctor|" + _abbr("SOX") + r"|필라델피아|파운드리|Foundry"),
    ("2차전지", r"2차\s*전지|이차전지|배터리|Batter|양극재|음극재|리튬|Lithium"),
    ("AI·테크", _abbr("AI") + r"|인공지능|Artificial|테크|" + _abbr("Tech") + r"|Technology|"
                r"소프트웨어|Software|클라우드|Cloud|데이터센터|Data Center|양자|Quantum|"
                r"로봇|Robot|플랫폼|인터넷|Internet|메타버스|" + _abbr("IT") + r"|"
                r"네트워크|Network|디지털|Digital"),
    ("바이오·헬스케어", r"바이오|Bio|헬스|Health|제약|Pharma|의료|Medical|비만|Obesity"),
    ("금융", r"금융|Financial|은행|Bank|보험|Insurance|증권|Broker"),
    ("리츠·부동산", r"리츠|REIT|Realty|부동산|Real Estate|Property"),
    ("에너지·원자력", r"에너지|Energy|원자력|Nuclear|우라늄|Uranium|수소|Hydrogen|"
                     r"태양광|Solar|풍력|Wind|전력|Electricity|Utilit|석유|Oil|가스|Gas"),
    ("방산·우주", r"방산|방위|Defen[sc]e|Aerospace|우주|Space|항공"),
    ("자동차", r"자동차|" + _abbr("Auto") + r"|Automobile|모빌리티|Mobility|전기차|"
              + _abbr("EV") + r"|스마트카"),
    ("게임·엔터", r"게임|Gam(e|ing)|엔터|Entertain|미디어|Media|콘텐츠|Content|K-?POP"),
    ("소비재", r"소비|Consumer|유통|Retail|식품|Food|농업|Agri|화장품|Cosmetic"),
    ("조선·기계", r"조선|Shipbuild|기계|Machinery|건설|Construction|중공업"),
    ("원자재·금", r"원자재|Commodit|골드|Gold|Silver|구리|Copper|귀금속|Precious|"
                 r"팔라듐|Palladium|백금|Platinum"),
    ("채권·금리", r"채권|Bond|국채|Treasury|Fixed Rate|회사채|Corporate|통안|CD금리|"
                 r"SOFR|KOFR|" + _abbr("KTB") + r"|MMF|단기자금|Preferred|특수채|국고채|"
                 r"크레딧|Credit|만기자동연장|금리"),
    ("배당주", r"배당|Dividend|고배당|인컴|Income|밸류업|주주가치"),
    ("자산배분·TDF", r"TDF|자산배분|Multi-?Asset|타겟데이트|Target Date|" + _abbr("TIF")),
    # '광범위지수'라고 부르다가 '혼합'으로 바꿨다 (특정 섹터가 아니라 여러 섹터가
    # 섞인 시장 대표지수라는 뜻. 38단계 사용자 요청)
    ("혼합", r"KOSPI|코스피|KOSDAQ|코스닥|KRX\s*\d|S&P\s*500|S&P500|NASDAQ\s*100|"
                  r"나스닥\s*100|Nifty|TOPIX|닛케이|CSI|MSCI|ACWI|Total Market|"
                  r"Equity Market|1200|STOXX\s*50|대표주|우량업종|5대그룹|수출주|내수주|"
                  # 'Select'·'iSelect' 는 지수 제공사 브랜드라 분류 근거가 못 된다
                  r"밸류|성장주|Growth|ESG"),
]

# ── 전략 (분배형 ETF는 이게 분배율을 좌우한다) ──────────────────
# ⚠ 레버리지·인버스는 보통 **펀드 구조**라 기초지수명에 안 나온다(예: KODEX 레버리지의
#   기초지수는 그냥 'KOSPI 200'). 그래서 여기서 잡히는 건 지수 자체가 레버리지·인버스인 경우뿐.
STRATEGY_RULES = [
    # 미국 상장 ETF 는 같은 구조를 'Option Income'·'Derivative Income'·'WeeklyPay'
    # 로 부른다 (YieldMax·Roundhill 계열). 이름만 다르고 커버드콜이다.
    ("커버드콜", r"커버드\s*콜|Covered\s*Call|옵션\s*프리미엄|타겟프리미엄|Premium|OTM|"
                r"Option Income|Derivative Income|WeeklyPay|Option Strategy"),
    # 미국은 배율을 '2X'·'3X'·'Ultra'·'Bull', 방향을 'Bear'·'Short' 로 쓴다.
    # 이런 상품은 분배율이 수백 %로 찍히면서 기준가가 그만큼 녹는다 — 반드시 갈라내야 한다.
    # 방향(인버스)이 배율보다 중요한 정보라 'Bear 3X' 는 인버스로 본다 → 먼저 검사한다.
    ("인버스", r"인버스|Inverse|" + _abbr("Short") + r"|-1X|" + _abbr("Bear")
              + r"|UltraShort"),
    ("레버리지", r"레버리지|Leverage|" + _abbr("2X") + r"|" + _abbr("3X") + r"|2배|3배|"
                r"Ultra(?!short)|" + _abbr("Bull") + r"|Daily Target|YieldBOOST"),
    ("혼합", r"혼합|Mixed|Multi-?Asset|채권혼합|국채혼합"),
]


def _match(text: str, rules: list[tuple[str, str]]) -> str:
    """규칙을 위에서부터 훑어 첫 번째로 걸리는 라벨. 없으면 빈 문자열."""
    if not text:
        return ""
    for label, pattern in rules:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return label
    return ""


def guess_region(index_name: str, market_class: str = "") -> str:
    """투자지역 추정. KRX 시장분류가 '국내'면 지수명과 무관하게 한국."""
    market = str(market_class or "").strip()
    if market == "국내":
        return "한국"
    hit = _match(str(index_name or ""), REGION_RULES)
    if hit:
        return hit
    if market == "국내&해외":
        return "국내+해외"
    return "기타" if market else ""


def guess_sector(index_name: str) -> str:
    return _match(str(index_name or ""), SECTOR_RULES) or "기타"


def guess_strategy(index_name: str) -> str:
    return _match(str(index_name or ""), STRATEGY_RULES) or "일반"


def attach(frame: pd.DataFrame, index_col: str = "기초지수명",
           market_col: str = "시장분류") -> pd.DataFrame:
    """기초지수명으로 투자지역·테마섹터·전략 컬럼을 붙인다."""
    out = frame.copy()
    if index_col not in out.columns:
        out["투자지역"] = ""
        out["테마섹터"] = ""
        out["전략"] = ""
        return out
    market = out[market_col] if market_col in out.columns else pd.Series("", index=out.index)
    out["투자지역"] = [guess_region(i, m) for i, m in zip(out[index_col], market)]
    out["테마섹터"] = out[index_col].map(guess_sector)
    out["전략"] = out[index_col].map(guess_strategy)
    return out
