# [ComponentName] SPEC

> 모든 섹션을 기계적으로 채우지 않는다. cross-component 계약, 소유 invariant, 외부에서 관찰 가능한 동작을 정의하지 않는 섹션은 삭제한다.
> 독자 우선(reader-first) 계약 문서.
> 간결하지만 필요한 내용은 빠짐없이 담는다. 템플릿은 준수하되, 컴포넌트가 정말 더 많은 설명을 필요로 하지 않는 한 보통 80~180줄 내외의 compact한 문서를 지향한다.
> 이 문서에는 이 컴포넌트가 무엇을 해야 하는지, 왜 존재하는지, 핵심 기술 선택, 그리고 다른 구성요소가 신뢰할 수 있는 계약/제약만 기록한다.
> 이 문서를 구현 계획서로 만들지 않는다.
>
> 이 문서에 포함할 것 (Belongs here):
> - 컴포넌트가 소유하는 책임
> - 컴포넌트의 존재 이유 / 성공 결과
> - 외부 계약, 불변조건, 제약, 상태 규칙
> - 선택한 기술 스택과 간단한 선정 근거
>
> 이 문서에 포함하지 말 것 (Does NOT belong here):
> - 알고리즘, 내부 모듈/클래스 구조, 실행 순서, 작업 분할, 파일 단위 구현 내용, 마이그레이션 단계, rollout choreography -> PLAN
> - 긴 설계 대안 비교, 결정 이력 -> ADR
> - 저가치 테스트 목록, 테스트 permutation 나열 -> PLAN / 테스트 코드
>
> 작성 규칙 (Authoring rules):
> - 서술형 문장보다 bullet/table을 우선한다.
> - 모든 bullet은 반드시 이 컴포넌트에 특화된 내용이어야 하며, generic filler는 삭제한다.
> - scalable / robust / maintainable 같은 모호한 근거는 구체적인 제약이나 계약에 연결되지 않으면 금지한다.
> - 한 섹션이 대략 5~7개 bullet 또는 작은 표 1개를 넘기기 시작하면 세부사항을 PLAN 또는 ADR로 이동한다.
> - 관련 없는 섹션은 통째로 삭제하고, 빈 placeholder는 남기지 않는다.
> - 구현 중 바뀔 수 있고 계약의 일부가 아닌 내용은 PLAN으로 보낸다.
> - 여러 대안 비교가 있었다면 여기에는 최종 선택안과 결정적 이유만 남기고, 비교 내용은 ADR로 보낸다.

**메타 정보 (Meta)**
- Component ID:
- SOT: `docs/system-design.md` (이 SPEC은 system design SOT와 일관되어야 한다)
- Related docs: [PRD], [PLAN], [ADR if any]
- Status: Draft / Approved / Superseded

---

## 1. Context & Scope

### 1.1 목적 (Purpose)
- **한 줄 요약:** 컴포넌트가 시스템에서 수행하는 핵심 역할.
- **비즈니스 목표:** (예: 업로드 완료 후 10분 이내 READY 달성)

### 1.2 요구 기술 스택 및 환경 변수 (Tech Stack & Configs)
- 언어/프레임워크, ORM/DB, 주요 외부 라이브러리
- 필수 환경 변수: `...`

### 1.3 경계 (Boundaries)
- **In-Scope:** 컴포넌트가 직접 책임지는 기능 나열.
- **Out-of-Scope:** 다른 컴포넌트에 위임하거나 이번 스펙에서 제외되는 기능 명시.

### 1.4 상태 라이프사이클 기준 (필요 시)
- 정상 전이: 예) `PENDING -> UPLOADED -> PROCESSING -> READY`
- 예외 전이: 예) `FAILED` (`failed_stage` 기록)

---

## 2. Contracts (Interface & Data)
> 외부 통신 규격과 저장소 접근을 **Delta 중심**으로 명세

### 2.1 API / Message Endpoint

#### [HTTP API]

- **Auth / Tenancy:** 예) JWT 필수, `user_id`로 테넌시 필터.

| HTTP Method | Endpoint (URI) | Request | Success Response | Notes |
| --- | --- | --- | --- | --- |
| POST | `/api/v1/...` | 주요 필드/제약 | 201/202 응답 스키마 | 분기 조건 등 |

- **스키마 제약 조건 (Pydantic 기준):** 주요 필드별 타입·범위·필수 여부를 나열한다.

#### [Object Storage] *(해당 시)*

- **StorageClient 인터페이스:** `generate_signed_url()`, `get_blob_metadata()`, `delete_object()` 등 메서드를 가진 추상 클래스를 정의한다. 구현체는 의존성 주입(DI)으로 교체 가능하다.
  - `(ConcreteStorageClient)` — 운영 환경 구현체
  - `InMemoryStorageClient` — 로컬/단위 테스트 전용 구현체 (더미 URL 반환, 내부 dict로 적재·삭제 상태 관리)

#### [Message Broker / 비동기 큐] *(해당 시)*

- **BrokerClient 인터페이스:** `publish(message)` 메서드를 가진 추상 클래스를 정의한다. 구현체는 의존성 주입(DI)으로 교체 가능하다.
  - `(ConcreteBrokerClient)` — 운영 환경 구현체
  - `InMemoryBrokerClient` — 로컬/단위 테스트 전용 구현체 (발행 메시지를 메모리 리스트에 누적)
- **Exchange / Topic, Routing Key, Queue:** (해당 브로커 구성 명시)
- **Message Contract:** `docs/system-design.md` MessageEnvelope와 동일한 필드를 사용한다.

```json
{
  "message_type": "PREPROCESS_REQUEST",
  "payload_version": "v1",
  "trace_id": "UUID4",
  "attempt": 1,
  "video_id": "UUID4",
  "issued_at": "ISO8601"
}
```

### 2.2 Data Access (Reads & Writes)
| Type | Store | Entity/Table | Key/Filter | Mutation/Action | Notes (트랜잭션/인덱스/보안) |
| --- | --- | --- | --- | --- | --- |
| Read | ... | ... | ... | SELECT | 테넌시/인덱스 |
| Write | ... | ... | ... | INSERT/UPDATE | 트랜잭션 경계 |

### 2.3 SLA & Constraints
- Timeout, Signed URL TTL, 파일 크기/확장자 제한, 페이지 limit 등
- 강제 방식까지 명시 (예: `content-length-range`, 완료 시 size 재검증)

### 2.4 Error Contract & Messaging Semantics
| HTTP Status | Error Code | 발생 조건 | Retryable |
| --- | --- | --- | --- |
| 400 | INVALID_ARGUMENT | 유효하지 않은 입력값 | N |
| 401 | UNAUTHENTICATED | JWT 미제공 또는 서명/만료 검증 실패 | N |
| 403 | FORBIDDEN | 테넌시 위반 (타인 리소스 접근) | N |
| 404 | NOT_FOUND | 미존재 리소스 접근 | N |
| 409 | CONFLICT | 허용되지 않은 상태 전이 시도 | N |
| 429 | RATE_LIMITED | 초당 요청 한도 초과 | Y (Backoff) |
| 500 | INTERNAL_ERROR | DB/외부 API 오류 | Y |
- **에러 응답 바디:** `{"code": "ERROR_CODE", "message": "설명 문자열", "trace_id": "UUID4"}`
- 멱등 응답 구분이 필요한 경우 (예: 최초 202, 중복 200) 별도 명시.
- MQ Ack/Nack, 재시도/Backoff, Poison/DLQ 정책 필요 시 추가.

### 2.5 스키마 (DDL) *(SOT 소유 컴포넌트 필수 / 읽기 전용 참조 컴포넌트는 "참조 스키마"로 명시)*

> SOT를 직접 관리하는 경우: 마이그레이션 도구(예: Alembic)로 관리하는 테이블 DDL을 전체 컬럼 기준으로 기술.
> 읽기 전용으로 참조하는 경우: 원본 관리 컴포넌트를 명시하고 접근 필드만 기재.

```sql
CREATE TABLE (table_name) (
    id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    UUID        NOT NULL,
    status     TEXT        NOT NULL DEFAULT '...' CHECK (status IN ('...')),
    deleted    BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_(table)_(key) ON (table_name)(...);
```

---

## 3. Core Design & Logic

### 3.1 주요 흐름 (Sequence)
- 핵심 플로우를 단계별 서술 (필요하면 Local/External 등 분기 별도 기술).

### 3.2 상태 전이 (State Machine)
> 본 컴포넌트가 직접 트리거하는 전이만 구현 대상이다. ★ 표시 행은 타 컴포넌트 주도 전이로, 참조 목적으로만 기재한다.

| From | To | Actor | Trigger | Guard | Side Effects |
| --- | --- | --- | --- | --- | --- |

### 3.3 멱등성 및 복구 (Resilience)
- 멱등 키/조건, 중복 메시지 처리 정책, 재시도/보정(Reconcile) 전략.

### 3.4 Data Consistency & Orphan Prevention
- 트랜잭션 경계, 잠금/유니크 제약, 보정/클린업(예: lifecycle 정책).
- 삭제 책임 분리 여부 명시.

---

## 4. Observability & Ops
- **Logging:** 필수 필드(`trace_id`, `user_id`, 도메인 id), 포맷.
- **Metrics:** 핵심 지표(예: `mq_publish_fail_count`, `reconciler_republish_count`, `cursor_decode_fail_count`, p95 latency 등).
- **Alerts:** 임계치 정의는 인프라팀에 위임. 주요 감시 대상(예: 큐 지연, 재발행 급증, 5xx 비율 등)만 명시.
- **Trace Propagation:** 수신 `trace_id` 전파 규칙.

---

## 5. Acceptance Criteria (DoD)

### 5.1 시나리오 검증

> 엔드포인트 또는 기능 단위로 섹션(`####`)을 나누고, 각 섹션 내에서 **정상** / **예외** 로 구분하여 `* [ ]` 체크박스로 기술한다.

#### (엔드포인트 또는 기능명 — 예: POST /api/v1/resource)

**정상**
* [ ] (정상 흐름 시나리오 — 분기가 있으면 분기별로 기술)

**예외**
* [ ] JWT 미제공 → 401
* [ ] (유효성 위반 케이스) → 400
* [ ] 타인 리소스 접근 → 403
* [ ] 미존재 리소스 → 404

#### (엔드포인트 2 — 필요한 만큼 반복)

**정상**
* [ ] ...

**예외**
* [ ] ...

#### (Reconciler / Background Worker 등 해당 시)

**정상**
* [ ] (재발행 트리거 조건 충족 건 → 재발행 + 상태 변경)
* [ ] (조건 미충족 건 → 재발행 없음)

### 5.2 검증을 위한 테스팅 전략 (Testing Strategy)

에이전트는 아래 가이드라인을 만족하는 자동화 테스트를 작성해야 한다.
* 테스트 프레임워크는 `pytest`, `pytest-asyncio`, `httpx`를 사용한다.
* **커버리지 목표:** 단위·통합 테스트 합산 (목표 %) 이상을 달성한다 (`pytest-cov` 기준).
* DB 통합 테스트는 PostgreSQL 기반의 격리 환경(Testcontainers 또는 Docker Compose)을 사용한다.
* **외부 의존성 격리 전략:**
  * Object Storage → `InMemoryStorageClient` (Test Double): 더미 URL 반환, 내부 dict로 적재·삭제 상태 관리.
  * Message Broker → `InMemoryBrokerClient` (Test Double): 실제 MQ 없이 동작, 발행 메시지를 메모리 리스트에 누적.
  * JWT 인증 → 테스트 전용 시크릿으로 실제 토큰을 생성하여 사용한다 (외부 인증 서버 호출 없음).
  * 기타 외부 HTTP API(해당 시) → `AsyncMock`으로 대체하여 외부 호출 없이 동작한다.
* (해당 시) 시간 의존 로직(Reconciler, TTL 등)은 시간 고정(freezegun 등)과 상태별 fixture를 활용하여 재발행 조건을 검증한다.
* (해당 시) Opaque Cursor 계약은 encode/decode round-trip 테스트와 잘못된 토큰에 대한 예외 케이스를 포함한다.

### 5.3 산출물 (Artifacts)

폴더 구조는 `docs/Tech_Spec/folder_structure.md`를 참조한다.

* [ ] HTTP 라우터 / 메시지 컨슈머 — (핵심 엔드포인트/큐 목록)
* [ ] Pydantic DTO — 요청/응답 스키마
* [ ] 비즈니스 서비스 — (주요 유스케이스: 상태 전이, 멱등성 등)
* [ ] ORM 모델 — (엔티티 목록)
* [ ] StorageClient / BrokerClient 인터페이스 및 구현체 (운영 + InMemory) — 해당 시
* [ ] (기타 인프라 어댑터 — 외부 API 클라이언트 등)
* [ ] 단위 테스트 / 통합 테스트
