# Candidate 준비 기간 online dual-write 제거 계획

## 목표

오프라인 평가를 통과한 candidate는 cutover로 active가 된 뒤부터만 신규 online ingest 데이터를 받는다. 로컬과 GCP에서 같은 코드 경로와 정책을 사용한다.

## 변경 범위

- Pipeline Worker는 `ModelRelease`의 현재 active model/index 한 곳만 읽고 한 번만 임베딩·저장한다.
- candidate 준비 기간의 active/candidate 다중 projection 자료구조와 반복 저장을 제거한다.
- cutover에서 candidate vector row 누락 조회와 `blocked_missing_candidate_rows` 결과를 제거한다.
- candidate 모델 readiness, legacy 재색인 gate, active→previous 전환, rollback 복구는 유지한다.
- `candidate_opened_at`은 운영 추적과 상태 검증을 위해 유지한다. DB migration은 추가하지 않는다.

## 핵심 검증

- `CANDIDATE_REINDEXING` 중 영상 한 건이 기존 active index/model에만 저장된다.
- candidate vector row가 없어도 readiness와 legacy 조건을 통과하면 cutover된다.
- cutover 후 candidate가 active, 기존 active가 previous가 된다.
- Pipeline Worker와 feedback-loop-pipeline의 전체 테스트가 통과한다.
- 기존 GCP 업로드 E2E가 신규 영상의 벡터가 현재 active model/index 한 곳에만 저장됐는지 확인한다.
- 두 서비스 Docker 이미지가 빌드되고 Terraform 구성이 유효하다.

## GCP 배포 순서

1. `model_release.release_status=STABLE`을 확인한다.
2. candidate 누락 gate를 제거한 feedback-loop-pipeline 이미지를 먼저 배포한다.
3. active-only Pipeline Worker 이미지를 배포한다.
4. cutover 이후 영상 한 건을 업로드한다.
5. 해당 영상의 `vector_index_entry`가 현재 `active_index_name`, `active_model_version`에만 존재하는지 확인한다.

Pipeline Worker를 먼저 배포하면 구버전 feedback-loop가 의도적으로 생성하지 않은 candidate row를 누락으로 판단해 cutover를 막을 수 있으므로 순서를 바꾸지 않는다.
