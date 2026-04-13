# [ComponentName] SPEC

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

## 1. 목적과 범위 (Purpose and Scope)

### 1.1 한 줄 요약
- [이 컴포넌트가 시스템에서 하는 일]

### 1.2 책임 경계
- In scope:
- Out of scope:
- Upstream dependencies:
- Downstream consumers:

### 간단한 흐름 (Simple Flow)

### 1.3 기술 스택 선택
| 영역 (Area) | 선택안 (Choice) | 왜 이 선택인가 |
| --- | --- | --- |
| Runtime / framework |  |  |
| Storage / DB |  |  |
| Messaging / async |  |  |
| Key libraries |  |  |

Notes:
- 각 행의 근거는 한 줄로 짧게 적고, 결정에 영향을 준 핵심 제약만 쓴다.
- tradeoff 분석이 길어지면 ADR을 작성하거나 링크한다.

---

## 2. 계약 (Contracts)

> 다른 컴포넌트, 운영자, 미래 구현자가 실제로 의존할 수 있는 계약만 정의한다.
> 다른 컴포넌트/운영자가 의존할 수 없는 내용이면 여기에 넣지 않는다.
> 외부에서 관찰 가능하거나 계약적으로 요구되는 경우가 아니라면 알고리즘, 내부 모듈 구조, 실행 순서, 작업 분해, 마이그레이션 단계는 금지한다.

### 2.1 외부 인터페이스

#### HTTP / RPC / Consumer 인터페이스
| Interface | Method / Trigger | Input summary | Output summary | Auth / tenancy | Notes |
| --- | --- | --- | --- | --- | --- |
|  |  |  |  |  |  |

#### 메시지 / 이벤트 계약 (해당 시)
- Topic / exchange / queue:
- Producer / consumer responsibility:
- Delivery semantics: at-most-once / at-least-once / exactly-once-like
- Payload versioning rules:

```json
{
  "event_type": "EXAMPLE_EVENT",
  "payload_version": "v1",
  "trace_id": "UUID4"
}
```

#### 외부 서비스 계약 (해당 시)
| Dependency | Used for | Required behavior / assumption | Failure impact |
| --- | --- | --- | --- |
|  |  |  |  |

### 2.2 데이터 계약

#### 소유 데이터 (이 컴포넌트가 SOT인 경우)
| Entity / table | Purpose | Key fields / invariants | Notes |
| --- | --- | --- | --- |
|  |  |  |  |

#### 참조 데이터 (다른 SOT를 읽는 경우)
| Source owner | Entity / table | Fields relied on | Read-only assumptions |
| --- | --- | --- | --- |
|  |  |  |  |

### 2.3 상태 및 비즈니스 규칙
- 항상 유지되어야 하는 불변조건:
- 이 컴포넌트가 소유하는 허용 상태 전이:
- 거부되어야 하는 전이 / invalid condition:
- Idempotency rule:
- Multi-tenant / authorization rule:

명확성이 높아질 때만 표를 사용한다:

| From | To | Trigger | Guard / rule | Required side effects |
| --- | --- | --- | --- | --- |
|  |  |  |  |  |

### 2.4 한계와 운영 제약
- Performance / latency target:
- Throughput / rate / concurrency limits:
- Payload / file size / pagination limits:
- Timeout / TTL / retry constraints:
- Security / privacy constraints:

### 2.5 에러 계약
| Surface | Condition | Code / status | Retryable | Notes |
| --- | --- | --- | --- | --- |
|  |  |  |  |  |

- 표준 에러 응답 형태:
```json
{"code":"ERROR_CODE","message":"human-readable summary","trace_id":"UUID4"}
```

---

## 3. 관측성과 운영 (Observability and Operations)

운영자와 구현자가 반드시 보존해야 하는 것만 기록한다.

- Required log fields:
- Key metrics / alerts worth tracking:
- Trace / correlation propagation rule:
- Reconciliation / cleanup requirement (if any):

---

## 4. 인수 기준 (Acceptance Criteria)

> 결과 중심으로 작성한다. 대략 3~7개의 critical outcome만 둔다.
> 각 기준은 외부에서 관찰 가능해야 하며, 비즈니스 규칙 또는 계약에 매핑되어야 한다.
> 여기에는 permutation이나 테스트 케이스를 나열하지 않는다. 상세 검증은 PLAN에 둔다.

### 4.1 반드시 통과해야 하는 시나리오
- [ ] 외부에서 관찰 가능한 핵심 성공 결과가 end-to-end로 성립한다.
- [ ] 가장 위험도가 높은 비즈니스 규칙 또는 불변조건이 강제된다.
- [ ] Authorization / tenancy boundary가 강제된다. (해당 시)
- [ ] 실패 처리가 선언된 외부 에러/행동 계약을 따른다.
- [ ] Idempotency / duplicate handling이 명세대로 동작한다. (해당 시)

### 4.2 비목표 / 보류 항목
- 이 spec에서 의도적으로 다루지 않는 항목을 명시한다.

---

