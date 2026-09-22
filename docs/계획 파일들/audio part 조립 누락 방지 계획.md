 네. 세 기준을 동시에 만족하려면 새 Queue나 DB version을 추가하지 않고, 기존 assembly cursor 충돌 시 최신 상태로 다시 조립하는 방식이 가장 적합합니다.

  ## 구현 방향

  현재 next_part_index, next_chunk_index가 이미 version 역할을 합니다.

  두 assembly가 동시에 실행됐을 때:

  1. 먼저 저장한 쪽이 cursor를 전진시킵니다.
  2. 늦게 저장한 쪽은 cursor_or_run_changed를 받습니다.
  3. 이때 끝내지 않고 최신 cursor와 완료된 part를 다시 읽습니다.
  4. 남은 part와 final flush를 처리할 때까지 계속합니다.

  이를 재수렴이라고 부를 수 있습니다. 다른 영상에는 영향을 주지 않고, 충돌한 영상만 최신 상태를 다시 확인합니다.

  ## 수정 계획

  ### 1. TranscriptAssemblyCoordinator.advance()만 재수렴 구조로 변경

  현재 한 번만 실행하는 내용을 _advance_once()로 분리합니다.

  advance()는 다음처럼 동작합니다.

  - 정상 저장 성공 → 종료
  - 조립할 part가 없음 / run 종료 / 삭제 중 → 종료
  - part_identity_changed → 기존처럼 종료
  - cursor_or_run_changed → 최신 snapshot부터 다시 실행

  충돌할 때만 재실행하고 다음은 추가하지 않습니다.

  - 별도 assembly_work 테이블
  - DB version 컬럼
  - 주기 scheduler
  - polling
  - 긴 DB lock
  - 고정된 sleep

  또한 직전 snapshot과 같은 cursor가 반복되면 오류로 종료해 예상하지 못한 무한 반복을 막습니다.

  ### 2. 로그를 구분

  cursor_or_run_changed를 최종 assembly.skipped로 남기지 않고 중간 상태인 assembly.retrying으로 기록합니다.

  최종적으로는 한 번만 다음 결과가 남게 합니다.

  - advanced
  - completed
  - 실제로 처리할 작업 없음
  - 비활성·삭제 상태

  따라서 다음 S4에서 “경합이 있었지만 복구됐는지”를 로그만으로 확인할 수 있습니다.

  ### 3. 동시 실행 회귀 테스트 추가

  이번 S4 상황을 의도적으로 재현합니다.

  - part A 조립이 cursor 0을 읽고 대기
  - part B의 STT 완료
  - 다른 assembly가 cursor 0을 읽음
  - A가 먼저 cursor를 1로 저장
  - B의 첫 저장은 cursor_or_run_changed
  - B가 최신 cursor 1을 다시 읽어 part B와 final flush 처리

  최종 검증값은 다음과 같습니다.

  - next_part_index == total_part_count
  - assembly_completed == true
  - 모든 예상 chunk 생성
  - WAITING_FRAME == 0
  - 중복 chunk 없음

  정상적인 단일 assembly는 artifact를 한 번만 읽는지도 함께 검사합니다.

  ## 성능에 병목이 생기지 않는 이유

  - 다른 영상끼리는 전혀 기다리지 않습니다.
  - DB lock을 잡은 채 GCS 파일을 읽지 않습니다.
  - 정상 경로에는 추가 조회나 재시도가 없습니다.
  - 충돌한 같은 영상만 다시 계산합니다.
  - 충돌은 다른 호출이 cursor를 실제로 전진시킨 경우이므로, 의미 없는 polling이 아닙니다.

  현재 S4처럼 audio part가 2개라면 보통 추가 실행은 한 번입니다.

  ## 검증 순서

  1. assembly unit/integration 테스트 실행
  2. 동시 완료 재현 테스트 반복 실행
  3. long 영상 소규모 테스트
  4. S4 전체 재실행
  5. 다음 조건 확인

  - 전체 영상 READY
  - 모든 Queue 0
  - WAITING_FRAME 0
  - terminal cursor_or_run_changed 0
  - 충돌 시 assembly.retrying 다음에 advanced 또는 completed
  - 기존 S4 1회차 대비 assembly·전체 처리시간 악화 없음

  예상 변경 범위는 production 코드 1개와 assembly 테스트 파일 정도입니다. DB migration과 인프라 변경은 없습니다.

  다만 이 계획은 이번에 확인된 동시 assembly 경합을 해결하는 최소 패치입니다. STT 저장 직후 Worker 프로세스가 강제 종료되는 별도 장애까지 완전히 복구하려면 나중에 durable assembly Queue가
  필요합니다.