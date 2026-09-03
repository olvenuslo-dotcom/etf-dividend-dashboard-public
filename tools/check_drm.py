# -*- coding: utf-8 -*-
"""DRM 봉투·키 파일 검사 — 커밋 전(pre-commit)과 푸시 전(pre-push).

회사 DRM(DRMONE)이 걸린 파일은 원래 형식이 아니라 암호화된 봉투가 된다.
그대로 커밋하면 저장소에는 열 수 없는 파일이 올라가고, 다른 PC에서 클론했을 때
`Excel file format cannot be determined` 로 터진다(2026-08-07 실제 발생).

검사 자리가 **두 곳**인 이유:
- 커밋 전(`--staged`, 기본): 작업트리의 스테이징 파일을 본다. 사고를 가장 이르게 잡는다.
- 푸시 전(`--push`): 실제로 **올라가는 커밋 안의 blob** 을 본다. 작업트리를 안 보므로
  `--no-verify` 로 우회한 커밋도, **훅이 안 깔린 다른 PC에서 만들어 들어온 커밋**도 잡힌다.
  16단계 사고가 정확히 후자였다 — 커밋한 PC와 터진 PC가 달랐다.

직접 돌려볼 수도 있다:  venv\\Scripts\\python.exe tools\\check_drm.py
"""
from pathlib import Path
import subprocess
import sys

# DRM 봉투 앞머리. 확장자와 무관하게 파일 맨 앞에 그대로 박힌다.
DRM_SIGNATURES = (b"\x9b DRMONE", b"DRMONE")
PEEK = 64          # 앞 64바이트만 본다


def _git(*args: str) -> bytes:
    """git 출력을 **바이트로** 받는다 — 한글 경로가 섞여도 깨지지 않게."""
    return subprocess.run(["git", *args], capture_output=True, check=True).stdout


def staged_files() -> list[str]:
    """이번 커밋에 올라가는(추가·수정된) 파일 목록."""
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
        capture_output=True, text=True, encoding="utf-8", check=True,
    )
    return [line for line in out.stdout.splitlines() if line.strip()]


def _blob_head(sha: str) -> bytes:
    """저장소에 들어 있는 blob 의 앞부분. 작업트리가 아니라 커밋된 내용을 본다."""
    return _git("cat-file", "blob", sha)[:PEEK]


def pushed_blobs(local_sha: str, remote_sha: str) -> list[tuple[str, str]]:
    """푸시될 커밋에 담긴 (경로, blob SHA) 목록.

    경로 대신 **blob SHA로** 내용을 꺼낸다. `sha:경로` 로 꺼내면 한글 경로의
    인코딩에 걸리는데, blob SHA는 16진수라 그럴 일이 없다.
    """
    if set(remote_sha) == {"0"}:       # 원격에 아직 없는 브랜치 → 전체를 본다
        rows = _git("ls-tree", "-r", "-z", local_sha).split(b"\0")
        found = []
        for row in rows:
            if not row.strip():
                continue
            meta, _, path = row.partition(b"\t")
            found.append((path.decode("utf-8", "replace"), meta.split()[2].decode()))
        return found

    # `--raw`: ":<구모드> <신모드> <구SHA> <신SHA> <상태>\0<경로>\0"
    parts = _git("diff", "--raw", "--diff-filter=ACM", "-z",
                 remote_sha, local_sha).split(b"\0")
    found = []
    for i in range(0, len(parts) - 1, 2):
        meta = parts[i].split()
        if len(meta) < 4:
            continue
        found.append((parts[i + 1].decode("utf-8", "replace"), meta[3].decode()))
    return found


def is_drm_wrapped(path: Path) -> bool:
    try:
        head = path.read_bytes()[:PEEK]
    except OSError:
        return False      # 읽을 수 없으면 판단하지 않는다
    return any(sig in head for sig in DRM_SIGNATURES)


# 올라가면 안 되는 파일 이름 (API 키·로그인 정보)
SECRET_NAMES = (".env", ".env.local")


def is_secret(name: str) -> bool:
    """키가 든 파일인가. `.env.example` 은 값이 비어 있으니 통과시킨다."""
    return Path(name).name in SECRET_NAMES


def _report(action: str, secrets: list[str], drm: list[str], remedy: str) -> int:
    """action 은 조사까지 붙여서 넘긴다 ('커밋을' / '푸시를')."""
    if secrets:
        print(f"{action} 멈췄습니다 — 키가 든 파일이 섞여 있습니다:", file=sys.stderr)
        for name in secrets:
            print(f"  - {name}", file=sys.stderr)
        print(f"\n한 번 올라가면 되돌리기 어렵습니다. {remedy}", file=sys.stderr)
        return 1

    if not drm:
        return 0

    print(f"{action} 멈췄습니다 — DRM이 걸린 파일이 섞여 있습니다:", file=sys.stderr)
    for name in drm:
        print(f"  - {name}", file=sys.stderr)
    print("\n이대로 올리면 다른 PC에서 열리지 않습니다.", file=sys.stderr)
    print("엑셀 산출물이면 다시 만드세요:  "
          "venv\\Scripts\\python.exe src\\classify.py", file=sys.stderr)
    return 1


def check_staged() -> int:
    """커밋 전 — 스테이징된 작업트리 파일을 본다."""
    staged = staged_files()
    return _report(
        "커밋을",
        [name for name in staged if is_secret(name)],
        [name for name in staged if is_drm_wrapped(Path(name))],
        "스테이징에서 빼세요:  git restore --staged <파일>",
    )


def check_push(lines: list[str]) -> int:
    """푸시 전 — 올라가는 커밋 안의 blob 을 본다 (작업트리를 보지 않는다).

    git 이 표준입력으로 `<로컬ref> <로컬SHA> <원격ref> <원격SHA>` 를 준다.
    """
    secrets: list[str] = []
    drm: list[str] = []
    for line in lines:
        field = line.split()
        if len(field) < 4:
            continue
        local_sha, remote_sha = field[1], field[3]
        if set(local_sha) == {"0"}:      # 브랜치 삭제 — 올라가는 내용이 없다
            continue
        for path, blob in pushed_blobs(local_sha, remote_sha):
            if is_secret(path):
                secrets.append(path)
            elif any(sig in _blob_head(blob) for sig in DRM_SIGNATURES):
                drm.append(path)

    return _report(
        "푸시를",
        sorted(set(secrets)),
        sorted(set(drm)),
        "그 파일을 지운 커밋을 새로 만들거나 이력에서 걷어내세요.",
    )


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--push" in argv:
        return check_push(sys.stdin.read().splitlines())
    return check_staged()


if __name__ == "__main__":
    from pathlib import Path as _Path
    sys.path.insert(0, str(_Path(__file__).resolve().parent.parent / "src"))
    from console import use_utf8_stdout   # 콘솔이 cp949여도 안 죽게
    use_utf8_stdout()
    sys.exit(main())
