# -*- coding: utf-8 -*-
"""DRM 봉투·키 파일이 저장소에 올라가지 못하게 막는지 확인.

특히 **푸시 전 검사**가 중요하다. 커밋 전 검사는 `--no-verify` 로 넘길 수 있고,
훅이 안 깔린 다른 PC에서 만들어진 커밋은 애초에 거치지도 않는다.
2026-08-07 사고가 정확히 그 모양이었다 — 커밋한 PC와 파일이 안 열려 터진 PC가 달랐다.

진짜 git 저장소를 임시 폴더에 만들어 돌린다(작업트리가 아니라 커밋된 내용을 봐야 해서).
"""
from pathlib import Path
import os
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))

import check_drm

DRM_BYTES = b"\x9b DRMONE\x00" + b"x" * 40
ZERO = "0" * 40


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(["git", *args], cwd=repo, capture_output=True,
                         text=True, encoding="utf-8", check=True)
    return out.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path, monkeypatch):
    """커밋 하나가 든 임시 저장소. check_drm 은 CWD 기준으로 git 을 부른다."""
    _git(tmp_path, "init", "-q", "-b", "master")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "test")
    (tmp_path / "정상.txt").write_text("보통 파일", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "첫 커밋")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _commit(repo: Path, name: str, data: bytes) -> str:
    (repo / name).parent.mkdir(parents=True, exist_ok=True)
    (repo / name).write_bytes(data)
    _git(repo, "add", "-f", name)
    _git(repo, "commit", "-q", "--no-verify", "-m", f"add {name}")
    return _git(repo, "rev-parse", "HEAD")


def test_깨끗한_저장소는_통과한다(repo):
    head = _git(repo, "rev-parse", "HEAD")
    assert check_drm.check_push([f"refs/heads/master {head} refs/heads/master {ZERO}"]) == 0


def test_우회해서_커밋한_DRM_파일이_푸시에서_잡힌다(repo):
    """`--no-verify` 로 커밋 검사를 넘겨도 여기서 걸린다."""
    base = _git(repo, "rev-parse", "HEAD")
    head = _commit(repo, "봉투.xlsx", DRM_BYTES)
    assert check_drm.check_push(
        [f"refs/heads/master {head} refs/heads/master {base}"]) == 1


def test_새_브랜치는_전체_트리를_본다(repo):
    """원격에 없는 브랜치면 비교 대상이 없다 → 커밋 전체를 훑어야 한다."""
    head = _commit(repo, "봉투.xlsx", DRM_BYTES)
    assert check_drm.check_push(
        [f"refs/heads/master {head} refs/heads/master {ZERO}"]) == 1


def test_한글_경로도_검사된다(repo):
    """blob SHA로 내용을 꺼내는 이유 — 경로 인코딩에 걸리면 검사가 조용히 샌다."""
    base = _git(repo, "rev-parse", "HEAD")
    head = _commit(repo, "output/분배ETF목록.xlsx", DRM_BYTES)
    found = dict(check_drm.pushed_blobs(head, base))
    assert "output/분배ETF목록.xlsx" in found
    assert check_drm.check_push(
        [f"refs/heads/master {head} refs/heads/master {base}"]) == 1


def test_키_파일도_막는다(repo):
    base = _git(repo, "rev-parse", "HEAD")
    head = _commit(repo, ".env", b"KRX_OPENAPI_KEY=abcd\n")
    assert check_drm.check_push(
        [f"refs/heads/master {head} refs/heads/master {base}"]) == 1


def test_env_example_은_통과한다(repo):
    """값이 비어 있는 견본은 올라가야 한다."""
    base = _git(repo, "rev-parse", "HEAD")
    head = _commit(repo, ".env.example", b"KRX_OPENAPI_KEY=\n")
    assert check_drm.check_push(
        [f"refs/heads/master {head} refs/heads/master {base}"]) == 0


def test_브랜치_삭제는_통과한다(repo):
    """로컬 SHA가 0이면 올라가는 내용이 없다 — 검사할 게 없다."""
    assert check_drm.check_push(
        [f"refs/heads/master {ZERO} refs/heads/master {ZERO}"]) == 0


def test_훅이_저장소에_들어_있다():
    """훅 파일 자체가 커밋돼 있어야 다른 PC에서 켤 수 있다."""
    for name in ("pre-commit", "pre-push"):
        hook = ROOT / ".githooks" / name
        assert hook.exists(), f".githooks/{name} 이 없습니다"
        assert "check_drm.py" in hook.read_text(encoding="utf-8")
    assert (ROOT / "tools" / "install_hooks.bat").exists()
