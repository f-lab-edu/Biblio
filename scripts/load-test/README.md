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
- `video_pipeline/`: 영상 fixture 검증, 가변 시나리오, 동시 complete 요청과 결과 저장
- `tests/common`, `tests/search`, `tests/batch`, `tests/video`: 책임별 단위 테스트

## 새 테스트 유형 추가

1. `<test_type>/session.py`에 실행 순서와 전용 설정을 정의한다.
2. `<test_type>/target.py`에서 `EmbeddingTarget`을 확장하고 target 이름, zone,
   배포 설정 키, probe를 명시한다.
3. `runner.py`에 CLI 명령과 session 연결만 추가한다.
4. `tests/<test_type>/`에 전용 테스트를 추가한다.

공통 계층에는 특정 테스트의 endpoint, fixture, workload 규칙을 추가하지 않는다.

## 영상 파이프라인 driver

fixture manifest에는 `short`, `medium`, `long` 파일의 경로와 실제 무결성 값을 적는다.
상대 경로는 manifest 파일이 있는 디렉터리를 기준으로 해석한다.

```json
{
  "fixtures": {
    "short": {
      "path": "fixtures/short.mp4",
      "sha256": "...",
      "duration_seconds": 150,
      "size_bytes": 123456
    },
    "medium": {
      "path": "fixtures/medium.mp4",
      "sha256": "...",
      "duration_seconds": 720,
      "size_bytes": 234567
    },
    "long": {
      "path": "fixtures/long.mp4",
      "sha256": "...",
      "duration_seconds": 1680,
      "size_bytes": 345678
    }
  }
}
```

먼저 요청을 보내지 않는 plan 명령으로 workload를 확인한다.

```bash
scripts/load-test/runner.sh video-pipeline-plan \
  --preset S3 \
  --fixtures-manifest /path/to/fixtures.json
```

BigQuery billing 자료로 예상 비용 범위를 확인하고 사용자가 승인한 뒤에만 live 명령을
실행한다. `APP_JWT_SECRET`, `REQUESTER_USER_ID`, `CORE_API_URL`이 필요하다. Cloud Run
IAM 인증을 쓰는 환경에서는 `--cloud-run-auth`도 지정한다.

```bash
scripts/load-test/runner.sh video-pipeline-run \
  --preset S3 \
  --fixtures-manifest /path/to/fixtures.json \
  --biblio-project-id PROJECT_UUID \
  --cloud-run-auth
```

요청 수, 동시성, 반복 횟수, fixture와 phase 지연은 각각 `--request-count`,
`--concurrency`, `--repeat-count`, `--fixture`, `--phase-delay`로 바꿀 수 있다. 기본
preset과 다른 실행은 결과에서 `is_baseline=false`로 기록된다.
