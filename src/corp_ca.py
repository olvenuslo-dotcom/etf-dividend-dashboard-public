# -*- coding: utf-8 -*-
"""회사 보안장비(HTTPS 가로채기) 뒤에서도 외부 데이터를 받게 해 준다.

증상: 야후·iShares·SPDR 호출이 전부 이렇게 죽는다.
    curl: (60) self signed certificate in certificate chain (19)   ← curl_cffi
    SSLError                                                        ← requests

이유: `git` 은 윈도우 인증서 저장소(schannel)를 보지만, 파이썬의 requests 는
certifi 번들만, curl_cffi 는 자기 번들만 본다. 보안장비의 루트 인증서가
그 번들에 없어서 검증이 실패한다.

해법: certifi 번들 + 회사 루트 인증서를 합친 PEM 을 만들어 환경변수로 물린다.
번들은 PC마다 다르므로 저장소에 올리지 않는다(.gitignore).
"""
from pathlib import Path
import os
import shutil

# 회사 루트 인증서를 찾아볼 자리. NODE_EXTRA_CA_CERTS 는 Node.js 용으로 이미
# 깔려 있는 경우가 많아 가장 먼저 본다.
CANDIDATES = (
    os.environ.get("NODE_EXTRA_CA_CERTS", ""),
    r"C:\certs\somansa-ca.cer",
)

BASE = Path(__file__).resolve().parent.parent
BUNDLE = BASE / "data" / "corp-ca-bundle.pem"   # 생성물 (gitignore 대상)


def _corp_cert() -> Path | None:
    """회사 루트 인증서 파일을 찾는다. 없으면 None (= 가로채기 없는 환경)."""
    for raw in CANDIDATES:
        if not raw:
            continue
        path = Path(raw)
        if path.is_file():
            return path
    return None


def build_bundle(force: bool = False) -> Path | None:
    """certifi + 회사 인증서를 합친 PEM 을 만들고 경로를 돌려준다."""
    cert = _corp_cert()
    if cert is None:
        return None
    if BUNDLE.exists() and not force:
        return BUNDLE

    import certifi

    text = Path(certifi.where()).read_text(encoding="utf-8")
    extra = cert.read_text(encoding="utf-8", errors="replace")
    if "BEGIN CERTIFICATE" not in extra:
        # DER(바이너리) 형식이면 이 방식으로는 못 붙인다 — 조용히 포기한다
        return None

    BUNDLE.parent.mkdir(parents=True, exist_ok=True)
    BUNDLE.write_text(text.rstrip() + "\n" + extra.strip() + "\n", encoding="utf-8")
    return BUNDLE


def apply() -> Path | None:
    """이 프로세스에 번들을 적용한다. 수집 스크립트 시작할 때 한 번 부르면 된다.

    이미 사용자가 직접 잡아 둔 값이 있으면 건드리지 않는다.
    """
    if os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("CURL_CA_BUNDLE"):
        return None

    bundle = build_bundle()
    if bundle is None:
        return None

    # requests / curl_cffi(yfinance) / 표준 ssl 이 각각 다른 이름을 본다
    for name in ("REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "SSL_CERT_FILE"):
        os.environ[name] = str(bundle)
    return bundle
