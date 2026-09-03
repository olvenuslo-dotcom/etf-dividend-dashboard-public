# -*- coding: utf-8 -*-
"""API 키·로그인 정보를 저장소에 올리지 않고 읽어 오는 곳.

**키를 소스나 커밋에 절대 넣지 않는다.** 저장소 루트의 `.env` 에 적어 두면
여기서 읽어 환경변수로 올린다. `.env` 는 .gitignore 대상이다.

`.env` 예시 (등호 앞뒤 공백 없이, 따옴표 없이):

    KRX_ID=내아이디
    KRX_PW=내비밀번호
    ECOS_API_KEY=발급받은키

이미 윈도우 환경변수로 잡아 뒀다면 그쪽이 우선이고 `.env` 는 무시된다.
"""
from pathlib import Path
import os

BASE = Path(__file__).resolve().parent.parent
ENV_FILE = BASE / ".env"


def load(path: Path | None = None) -> list[str]:
    """`.env` 를 읽어 환경변수로 올린다. 올린 키 이름들을 돌려준다.

    이미 환경에 있는 값은 덮어쓰지 않는다(윈도우 설정이 우선).
    """
    path = path or ENV_FILE
    if not path.is_file():
        return []

    loaded = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip()
        # 따옴표로 감싸 둔 경우까지 받아 준다
        value = value.strip().strip('"').strip("'")
        if not name or os.environ.get(name):
            continue
        os.environ[name] = value
        loaded.append(name)
    return loaded


def missing(*names: str) -> list[str]:
    """아직 없는 키 이름들. 화면에 '무엇을 넣어야 하는지' 알릴 때 쓴다."""
    load()
    return [name for name in names if not os.environ.get(name)]


def has(*names: str) -> bool:
    """필요한 키가 전부 있나."""
    return not missing(*names)
