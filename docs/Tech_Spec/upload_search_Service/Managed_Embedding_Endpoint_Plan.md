# Managed Embedding Endpoint PLAN

**메타 정보**
- Component ID: `managed-embedding-endpoint`
- SOT: `docs/system-design.md`
- Target SPEC: `docs/Tech_Spec/upload_search_Service/Managed_Embedding_Endpoint_Spec.md`
- 관련 문서:
  - `docs/Tech_Spec/upload_search_Service/Search_Service_Plan.md`
  - `docs/Tech_Spec/upload_search_Service/Pipeline_Worker_Plan.md`
  - `docs/Tech_Spec/feedback_loop_&_admin_ops/Model_Release_and_Reindex_Spec.md`
- Plan status: Draft

---

## 1. 구현 의도

### 1.1 전달 목표
- 이 plan이 끝났을 때 실제로 동작해야 하는 것:
  - `/embed`는 호출자가 지정한 ready `model_version`으로 embedding을 반환한다.
  - active / previous / candidate 모델 런타임 준비 상태가 `ModelRelease` 계약과 맞게 표현된다.
  - Search Service와 Pipeline Worker가 같은 model-version 계약으로 endpoint를 호출한다.
- 검증 가능한 형태로 입증되어야 하는 것:
  - ready 상태인 model version은 성공하고, 누락된 model version은 400, unknown/not-ready model version은 503으로 실패한다.
  - candidate model은 release/reindex 목적에만 사용된다.
  - request/response shape와 trace/error 계약이 자동화 테스트로 고정된다.

### 1.2 이번 구현의 범위
- 이번 plan에 포함:
  - request schema에 `model_version`을 포함하도록 embedding API 계약 정렬
  - model version을 key로 사용하는 readiness registry 구성
  - active / previous / candidate sync path와 readiness 관측성
  - Search Service / Pipeline Worker embedding client 계약 테스트 갱신
- 명시적 제외 / 후속 phase:
  - `ModelRelease` DB schema migration
  - vector index cutover와 rollback orchestration
  - model quality evaluation
  - user/project authorization

### 1.3 전제조건과 blocker
- 이미 고정된 spec contract:
  - `ModelRelease`는 serving model/index의 SOT다.
  - Managed Embedding Endpoint 런타임 상태는 release sync에서 파생된다.
  - 호출자는 embedding 요청 시 target `model_version`을 전달한다.
- 필요한 upstream work / dependency:
  - active, previous, candidate version의 model artifact ref
  - Search Service와 Pipeline Worker의 `ModelRelease` read model 접근
  - 내부 서비스 인증 방식
- 구현을 막는 open question:
  - 없음.

### 1.4 구현 전략
- 전체 접근:
  - `/embed` 계약은 가능한 한 단순하게 유지하고, 내부에 target model routing과 readiness registry를 둔다.
- 핵심 기술 작업:
  - DTO/client 계약 갱신
  - model version 기준 runtime registry 도입
  - release sync 처리와 readiness reporting 추가
  - downstream 계약 테스트 갱신
- 리스크 축소 전략:
  - active 모델 1개만 ready인 경우를 compatibility case로 유지한다.
  - 실제 model artifact를 사용하기 전에 deterministic runtime double로 active/previous/candidate routing을 검증한다.
- 병합 전략:
  - 기본은 문서 계약에 맞춘 단일 구현 PR로 진행하되, code churn이 크면 API contract/client 갱신과 runtime registry 작업을 분리한다.
- Spec 추적 기준:
  - Managed Embedding Endpoint SPEC §2.1, §2.3, §4.1

---

## 2. Workstream과 순서

### 2.1 권장 순서
| 순서 | Workstream | SPEC 매핑 | 지금 필요한 이유 | 의존성 |
| --- | --- | --- | --- | --- |
| 1 | 계약과 client 정합성 | §2.1, §2.5 | runtime 변경 전에 호출자가 `model_version` 계약에 합의해야 한다 | 현재 endpoint test |
| 2 | Runtime model registry | §2.2, §2.3 | readiness는 model version 기준으로 관리되어야 한다 | Workstream 1 |
| 3 | Release sync와 readiness | §2.1, §3 | release/reindex에는 운영자가 볼 수 있는 model readiness가 필요하다 | Workstream 2 |
| 4 | 검증과 rollout 안전장치 | §4.1 | cross-service contract drift가 주요 delivery risk다 | Workstreams 1-3 |

### 2.2 Workstream 상세

#### Workstream: Contract and client alignment
- 목표:
  - `/embed` request validation과 downstream client가 target `model_version`을 전달하도록 맞춘다.
- SPEC 매핑:
  - §2.1 `/embed` request / response, §2.5 error contract
- 주요 변경:
  - embedding request DTO에 `model_version` 추가
  - Search Service와 Pipeline Worker embedding client가 release context에서 읽은 target model version을 보내도록 갱신
  - trace/error response shape는 유지
- 영향 가능 파일 / 영역:
  - managed endpoint schema와 API test
  - Search Service embedding client
  - Pipeline Worker embedding client
- 의존성 / 통합 지점:
  - Search Service와 Pipeline Worker의 `ModelRelease` read model
- 완료 조건:
  - contract test가 missing `model_version`에서는 실패하고 ready model version에서는 성공한다.
- 검증:
  - valid, missing, not-ready model version API test
  - request payload를 검증하는 downstream client unit test

#### Workstream: Runtime model registry
- 목표:
  - model version을 key로 사용하는 runtime registry를 도입한다.
- SPEC 매핑:
  - §2.2 owned runtime data, §2.3 state rules
- 주요 변경:
  - model version별 ready/not-ready 상태 추적
  - 각 `/embed` 요청을 요청된 runtime으로 route
  - 입력 순서와 all-or-nothing semantics 보존
- 영향 가능 파일 / 영역:
  - managed endpoint model state, loader/runtime boundary, inference service
- 의존성 / 통합 지점:
  - model artifact loader
  - admission control
- 완료 조건:
  - active와 previous test runtime이 cross-routing 없이 서로 다른 요청을 처리할 수 있다.
- 검증:
  - registry 상태 전이 unit test
  - requested model routing API test

#### Workstream: Release sync and readiness
- 목표:
  - release/reindex 작업이 active, previous, candidate model readiness를 endpoint runtime state에 동기화하게 한다.
- SPEC 매핑:
  - §2.1 serving model sync, §3 observability
- 주요 변경:
  - serving model set을 처리하는 `POST /internal/model-sync` 추가
  - health/readiness에서 ready model version 보고
  - sync 성공/실패 로그와 metric 추가
- 영향 가능 파일 / 영역:
  - managed endpoint internal model-sync route
  - health/readiness handler
  - observability hook
- 의존성 / 통합 지점:
  - Model Release and Reindex handoff
  - Model Artifact Files
- 완료 조건:
  - candidate sync가 candidate model을 로드하더라도 사용자 검색 기본 target으로 만들지 않는다.
- 검증:
  - active-only, active+previous, active+candidate fixture 기반 release-sync test
  - missing active model readiness test

#### Workstream: Verification and rollout safety
- 목표:
  - rollout 동안 Search, Worker, Model Release 계약이 계속 정렬되어 있음을 증명한다.
- SPEC 매핑:
  - §4.1 acceptance criteria
- 주요 변경:
  - endpoint, Search Service, Pipeline Worker test가 공유하는 contract fixture 추가
  - single-model compatibility와 multi-model readiness rollout check 문서화
  - model-not-ready와 sync failure metric/alert 추가
- 영향 가능 파일 / 영역:
  - service test
  - README / runbook
  - CI commands
- 의존성 / 통합 지점:
  - Search Service active/previous serving path
  - Pipeline Worker active/candidate dual-write path
- 완료 조건:
  - integration evidence가 active, previous, candidate 호출이 의도한 model version으로 resolve됨을 보여준다.
- 검증:
  - targeted unit/API test와 fake runtime 기반 contract smoke

### 2.3 병렬화와 병합 지점
- 병렬 가능한 작업:
  - DTO 합의 이후 downstream client request-shape test와 runtime registry test는 병렬 진행할 수 있다.
- 공유 통합 지점 / 충돌 가능 영역:
  - embedding request schema
  - model readiness representation
  - health response shape
- 최종 통합 체크포인트:
  - endpoint API test, Search embedding client test, Pipeline Worker embedding client test를 함께 실행한다.

---

## 3. 검증 및 테스트 전략

### 3.1 리스크 기반 테스트 초점
| Spec ref | 리스크 / 비즈니스 규칙 | 중요한 이유 | 권장 test level | 계획된 증명 |
| --- | --- | --- | --- | --- |
| SPEC §2.1 | 호출자가 `model_version`을 누락하거나 잘못 보냄 | embedding이 잘못된 model/index projection에 기록될 수 있다 | API + client unit | request validation과 client payload assertion |
| SPEC §2.3 | candidate model이 기본 검색 target으로 노출됨 | candidate data가 사용자 검색에 노출될 수 있다 | integration / contract | Search test는 사용자 query에서 active/previous만 요청함 |
| SPEC §2.3 | runtime registry가 잘못된 모델로 route | active/previous/candidate projection drift가 생긴다 | unit + API | fake runtime이 version-specific vector를 반환함 |
| SPEC §3 | `ModelRelease`와 readiness drift | unloaded model에서 cutover 또는 rollback이 진행될 수 있다 | integration / operational | sync/health test가 ready model set을 노출함 |

### 3.2 계획된 자동화 테스트
| Spec ref / acceptance criterion | 시나리오 / 규칙 | Test level | 이 level을 쓰는 이유 | 관찰 가능한 증거 |
| --- | --- | --- | --- | --- |
| AC 1 | ready model embedding 성공 | API | request routing과 response shape가 함께 동작한다 | text마다 vector 1개 |
| AC 2 | unknown 또는 not-ready model 실패 | API | runtime registry와 error mapping이 함께 동작한다 | 503 standard error |
| AC 3 | active/previous/candidate readiness 표현 | unit + API | registry state가 핵심 도메인 로직이다 | health가 기대한 ready version을 나열함 |
| AC 4 | Search active/previous request payload | client unit | cross-service drift를 방지한다 | payload에 기대한 `model_version` 포함 |
| AC 5 | Worker active/candidate request payload | client unit | candidate dual-write를 보호한다 | payload에 target model version 포함 |
| AC 6 | trace/error contract | API | middleware와 service error가 함께 동작한다 | response/log capture에 `trace_id` 보존 |

### 3.3 자동화 테스트로 다루지 않는 항목
| Spec ref / rule | 자동화하지 않는 이유 | 수동 / 운영 증거 |
| --- | --- | --- |
| Real model quality | provider/model output은 CI에서 충분히 deterministic하지 않다 | 고정 text를 사용한 staging smoke |
| Full rollback cutover | Model Release and Reindex가 소유한다 | release/reindex integration evidence |
| Production memory pressure | 배포된 model artifact와 hardware에 좌우된다 | rollout dashboard와 load smoke |

### 3.4 테스트 환경과 double
- DB / storage / broker 설정:
  - endpoint unit test에는 DB가 필요하지 않다.
  - release sync test는 fake artifact ref와 fake runtime을 사용한다.
- 외부 의존성 격리 방식:
  - model loader와 runtime은 CI에서 deterministic double을 사용한다.
- 시간 / async / retry 제어 방식:
  - 실제 sleep은 피하고, fake를 통해 load failure와 not-ready 상태를 만든다.
- 필요한 fixture 또는 seed data:
  - active-only release set
  - active+previous release set
  - active+candidate release set

### 3.5 검증 명령과 quality gate
- 필수 명령:
  - `cd services/managed-embedding-endpoint && poetry run pytest`
  - `cd services/search-service && poetry run pytest tests/unit/test_embedding_client.py`
  - `cd services/pipeline-worker && poetry run pytest tests/unit/test_embedding_client.py`
- 병합 전 최소 검증:
  - endpoint API test가 requested model routing을 검증한다.
  - Search/Worker client test가 target model version payload를 포함한다.
  - health/readiness test가 multi-model readiness를 검증한다.
- 첨부할 증거:
  - 세 command group의 test output
  - fake multi-model runtime의 sample health payload

---

## 4. 전달 리스크와 안전장치

| 리스크 | 영향 | 완화책 | 검증 |
| --- | --- | --- | --- |
| Request schema drift | Search/Worker call이 runtime에서 실패 | endpoint와 client를 같은 delivery slice에서 갱신 | contract test |
| Wrong model routing | vector projection이 `ModelRelease`와 불일치 | 명시적 `model_version`으로 route하고 version-specific fake vector로 테스트 | runtime registry test |
| Candidate readiness ambiguity | cutover가 너무 일찍 시작될 수 있음 | health/readiness가 ready model version을 노출 | release-sync test |
| Raw text logging | 민감한 사용자 content 노출 | count, size, model version, trace id만 log | log assertion / review |

---

## 5. Rollout and Rollback

### 5.1 Rollout 계획
- Migration / schema 단계:
  - endpoint가 소유하는 DB migration은 없다.
- Config / secret / infra 변경:
  - active, previous, candidate version의 model artifact ref를 설정한다.
  - internal service auth는 기존 service mesh 또는 gateway policy와 일관되게 유지한다.
- Backward / forward compatibility 고려사항:
  - `/embed`가 `model_version`을 요구하므로 endpoint DTO와 Search/Worker client rollout을 조율해야 한다.
  - active model 1개만 있는 배포도 초기 serving set으로 유효하다.
- Rollout 중 볼 monitoring signal:
  - model sync failure
  - model version별 `/embed` 503
  - health ready model version count
- 배포 후 확인:
  - active model smoke embedding
  - release state가 요구할 때 previous/candidate smoke

### 5.2 Rollback 계획
- App rollback:
  - request schema 변경으로 production failure가 발생하면 endpoint와 client를 함께 되돌린다.
- Data rollback 또는 safe-forward plan:
  - endpoint가 소유하는 durable data rollback은 없다.
- Async / message compatibility fallback:
  - 해당 없음. endpoint는 동기 HTTP다.
- 부분 배포 복구:
  - client가 `model_version`을 보내지만 endpoint가 이를 받지 못하면, traffic을 호환 가능한 endpoint version으로 되돌리거나 전환 기간 동안 tolerant parsing이 가능한 endpoint를 먼저 배포한다.

---

## 6. 완료 체크리스트

- [ ] 모든 계획된 workstream이 target SPEC에 매핑된다.
- [ ] 계획된 테스트가 SPEC section 또는 acceptance criteria에 매핑된다.
- [ ] active / previous / candidate readiness가 operator-visible evidence로 검증된다.
- [ ] Search Service와 Pipeline Worker embedding clients가 target `model_version`을 전송한다.
- [ ] 필요한 observability와 failure-path 점검이 포함되어 있다.
- [ ] rollout / rollback 단계에 compatibility 가정과 monitoring signal이 포함되어 있다.
- [ ] 남아 있는 open question 또는 deferred item이 기록되어 있다.
