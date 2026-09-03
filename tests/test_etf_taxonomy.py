# -*- coding: utf-8 -*-
"""분류 추정 테스트: 기초지수명 → 투자지역·테마섹터·전략."""
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import etf_taxonomy as tax


def test_시장분류가_국내면_무조건_한국():
    # 'KIS 미국채30년' 은 국내 상장 국내지수 취급인 경우가 있다 → 시장분류를 따른다
    assert tax.guess_region("KIS 미국채30년 엔화노출 지수", "국내") == "한국"


def test_해외는_지수명에서_지역을_뽑는다():
    assert tax.guess_region("NASDAQ 100", "해외") == "미국"
    assert tax.guess_region("Solactive United States Technology 100", "해외") == "미국"
    assert tax.guess_region("CSI A100", "해외") == "중국"
    assert tax.guess_region("TOPIX", "해외") == "일본"
    assert tax.guess_region("MarketVector Digital India Index", "해외") == "인도"
    assert tax.guess_region("FactSet TSMC 파운드리 밸류체인 지수", "해외") == "대만"
    assert tax.guess_region("EURO STOXX 50 Index", "해외") == "유럽"


def test_못_뽑으면_기타():
    assert tax.guess_region("알수없는 지수", "해외") == "기타"


def test_시장분류가_없으면_빈값():
    # KRX 정보를 아직 안 받은 상태에서 억지로 채우지 않는다
    assert tax.guess_region("알수없는 지수", "") == ""


def test_국내와_해외_섞인_건_따로_표시():
    assert tax.guess_region("Bloomberg Multi-Asset Weighted 지수", "국내&해외") == "국내+해외"


def test_한글_뒤_영문약어도_잡는다():
    # '온디바이스AI' 는 \bAI\b 로는 안 걸린다
    assert tax.guess_sector("FnGuide 온디바이스AI 지수") == "AI·테크"
    assert tax.guess_sector("FnGuide IT플러스 지수") == "AI·테크"


def test_섹터_대표_사례():
    assert tax.guess_sector("NYSE Semicondoctor Index") == "반도체"
    assert tax.guess_sector("iSelect 2차전지양극재 지수") == "2차전지"
    assert tax.guess_sector("KAP 27-12 특수채 총수익 지수(AAA이상)") == "채권·금리"
    assert tax.guess_sector("KOFR 지수") == "채권·금리"
    assert tax.guess_sector("Dow Jones U.S. High Dividend 10 Index") == "배당주"
    assert tax.guess_sector("Solactive Global Realty Income Index") == "리츠·부동산"
    assert tax.guess_sector("Indxx Hydrogen Economy Index") == "에너지·원자력"
    assert tax.guess_sector("FnGuide 스마트카 지수") == "자동차"
    assert tax.guess_sector("iSelect 전략적자산배분 TDF 2030 지수") == "자산배분·TDF"


def test_개별종목_지수는_섹터로_묶지_않는다():
    assert tax.guess_sector("KRX 삼성전자 지수") == "개별종목"
    assert tax.guess_sector("KEDI 엔비디아고정테크100 지수") == "개별종목"


def test_iSelect_는_분류_근거가_아니다():
    # 'Select' 가 지수 제공사 브랜드라 혼합(시장 대표지수)으로 오분류되면 안 된다
    assert tax.guess_sector("iSelect 미래전략기술 지수 (PR)") == "기타"


def test_커버드콜_전략():
    assert tax.guess_strategy("S&P500 10% Daily Fixed Covered Call Index") == "커버드콜"
    assert tax.guess_strategy("KEDI 미국 배당100 90%참여+데일리옵션 프리미엄 지수") == "커버드콜"
    assert tax.guess_strategy("KOSPI 200") == "일반"


def test_attach_로_세_칸이_한번에():
    frame = pd.DataFrame({
        "기초지수명": ["NYSE Semicondoctor Index", "코리아 밸류업 지수"],
        "시장분류": ["해외", "국내"],
    })
    out = tax.attach(frame)
    assert list(out["투자지역"]) == ["미국", "한국"]
    assert list(out["테마섹터"]) == ["반도체", "배당주"]
    assert list(out["전략"]) == ["일반", "일반"]


def test_기초지수명_컬럼이_없어도_죽지_않는다():
    out = tax.attach(pd.DataFrame({"종목명": ["아무거나"]}))
    assert list(out["투자지역"]) == [""]
