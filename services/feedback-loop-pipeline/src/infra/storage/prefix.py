from __future__ import annotations

# 저장소 구현(gcs/local/inmemory)이 공유하는 경로 prefix 헬퍼.
# prefix는 startswith로 매칭하므로, 끝에 / 를 강제해 옆 폴더 오매칭을 막는다.
# 예: "models/x" 가 "models/x-v2/..." 까지 잘못 잡는 것을 방지.


def normalize_prefix(prefix: str) -> str:
    # 앞뒤 / 를 모두 정리한 뒤 끝에 / 하나만 붙여 형식을 통일한다.
    # 빈 prefix는 전체 매칭(버킷 통째 복사) 사고를 막으려고 거부한다.
    # 예: "/models/x/" -> "models/x/"
    normalized = prefix.strip("/")
    if not normalized:
        raise ValueError("prefix must not be empty")
    return f"{normalized}/"


def relative_name(storage_path: str, prefix: str) -> str:
    # 전체 경로에서 prefix(폴더 부분)를 떼고 상대 경로만 남긴다.
    # 예: ("models/x/a.json", "models/x/") -> "a.json"
    return storage_path.removeprefix(prefix).lstrip("/")


def join_prefix(prefix: str, relative: str) -> str:
    # prefix 와 상대 경로를 / 하나로 잇는다. 끝 / 중복은 rstrip 으로 막는다.
    # 예: ("models/y/", "a.json") -> "models/y/a.json"
    return f"{prefix.rstrip('/')}/{relative}"
