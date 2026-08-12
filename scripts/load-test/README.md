# Load-test script structure

최상위에는 모든 부하테스트가 공유하는 실행 기반만 둔다.

- `runner.py`, `runner.sh`: CLI 진입점과 명령 연결
- `infrastructure.py`: VM, SSH, Terraform, 상태 파일 처리
- `k6_runner.py`: k6 실행, 결과 수집, 공통 판정
- `embedding_target.py`: endpoint 준비·격리·배포 정보와 VM 계측
- `load_config.py`: 공통 입력값 검증
- `remote/`: 원격 runner와 target에서 실행하는 공통 스크립트

테스트 유형별 코드는 별도 패키지에 둔다.

- `search_embedding/`: 검색 임베딩 session과 target probe
- `batch_embedding/`: 배치 임베딩 session, target probe, fixture 도구
- `tests/common`, `tests/search`, `tests/batch`: 책임별 단위 테스트

## 새 테스트 유형 추가

1. `<test_type>/session.py`에 실행 순서와 전용 설정을 정의한다.
2. `<test_type>/target.py`에서 `EmbeddingTarget`을 확장하고 target 이름, zone,
   배포 설정 키, probe를 명시한다.
3. `runner.py`에 CLI 명령과 session 연결만 추가한다.
4. `tests/<test_type>/`에 전용 테스트를 추가한다.

공통 계층에는 특정 테스트의 endpoint, fixture, workload 규칙을 추가하지 않는다.
