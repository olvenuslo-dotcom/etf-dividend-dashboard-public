# -*- coding: utf-8 -*-
from pathlib import Path
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

import env_keys
import check_drm


def test_env_파일을_읽어_환경변수로_올린다(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("KRX_ID=someone\nKRX_PW=secret\n", encoding="utf-8")
    monkeypatch.delenv("KRX_ID", raising=False)
    monkeypatch.delenv("KRX_PW", raising=False)

    loaded = env_keys.load(path)

    assert set(loaded) == {"KRX_ID", "KRX_PW"}
    assert os.environ["KRX_ID"] == "someone"


def test_이미_있는_환경변수는_덮어쓰지_않는다(tmp_path, monkeypatch):
    # 윈도우에 직접 잡아 둔 값이 우선이어야 한다
    path = tmp_path / ".env"
    path.write_text("KRX_ID=from_file\n", encoding="utf-8")
    monkeypatch.setenv("KRX_ID", "from_windows")

    env_keys.load(path)

    assert os.environ["KRX_ID"] == "from_windows"


def test_주석과_빈줄과_따옴표를_처리한다(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text('# 주석\n\nECOS_API_KEY="quoted"\n', encoding="utf-8")
    monkeypatch.delenv("ECOS_API_KEY", raising=False)

    env_keys.load(path)

    assert os.environ["ECOS_API_KEY"] == "quoted"


def test_파일이_없으면_조용히_넘어간다(tmp_path):
    assert env_keys.load(tmp_path / "없는파일") == []


def test_없는_키를_알려준다(monkeypatch):
    monkeypatch.delenv("어디에도없는키", raising=False)

    assert "어디에도없는키" in env_keys.missing("어디에도없는키")


def test_커밋훅이_env_파일을_막는다():
    assert check_drm.is_secret(".env") is True
    assert check_drm.is_secret("some/dir/.env") is True


def test_커밋훅이_env_example_은_통과시킨다():
    # 값이 비어 있는 견본이라 올라가도 된다
    assert check_drm.is_secret(".env.example") is False
