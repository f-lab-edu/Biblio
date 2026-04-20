# [ComponentName] PLAN

> 대상 SPEC을 구현하기 위한 실행 문서.
> 이 문서는 전략, 순서, workstream, 의존성, 검증, rollout을 통해 작업을 어떻게 전달할지 설명한다.
>
> 이 문서에 포함할 것 (Belongs here):
> - 구현 접근 방식과 순서
> - 작업 분해와 책임 경계
> - 의존성, blocker, migration path, rollout / rollback
> - validation strategy와 test focus
>
> 이 문서에 포함하지 말 것 (Does NOT belong here):
> - SPEC의 계약을 그대로 다시 길게 쓰는 것
> - 긴 설계 근거 또는 옵션 비교 -> ADR
> - 실행 가치가 거의 없는 과도한 task 나열
>
> 작성 규칙 (Authoring rules):
> - narrative completeness보다 execution을 우선한다.
> - 특별한 이유가 없다면 기본적으로 2~5개의 workstream으로 구성한다.
> - 잘게 쪼갠 task 다수보다 high-signal workstream 몇 개를 선호한다.
> - delivery risk 때문에 재서술이 꼭 필요한 경우가 아니라면 계약은 다시 쓰지 말고 SPEC section / acceptance criteria를 참조한다.
> - 모든 workstream은 user-visible, operator-visible, 또는 contract-relevant outcome을 만들어야 한다.
> - 모든 work item은 concrete outcome과 meaningful verification method를 가져야 한다.
> - SPEC이 아직 불안정하면 세부사항을 상상해서 채우지 말고 blocker로 적는다.

**메타 정보 (Meta)**
- Component ID:
- SOT: `docs/system-design.md` (이 PLAN은 system design SOT를 구현하며 그 내용과 일관되어야 한다)
- Target SPEC:
- Related docs: [ADR if any], [related specs]
- Plan status: Draft / Ready / In Progress / Done

---

## 1. 구현 의도 (Implementation Intent)

### 1.1 전달 목표
- 이 plan이 끝났을 때 실제로 동작해야 하는 것:
- 검증 가능한 형태로 입증되어야 하는 것:

### 1.2 이번 구현의 범위
- Included in this plan:
- Explicitly excluded / later phase:

### 1.3 전제조건과 blocker
- 이미 고정된 spec contract:
- 필요한 upstream work / dependency:
- 구현을 막는 open question:

### 1.4 구현 전략
- Overall approach:
- Key technical moves or slices:
- Risk reduction strategy:
- Merge strategy (single PR / phased PRs / parallel workstreams):
- Spec traceability anchor(s): [이 plan이 전달하는 SPEC section 및/또는 acceptance criteria]

---

## 2. Workstream과 순서 (Workstreams and Sequence)

> 레이어 자체보다 전달 가능한 기능 슬라이스를 기준으로 구성한다.
> 각 workstream은 독립적으로 이해 가능하고 검증 가능해야 한다.
> 각 workstream은 반드시 SPEC section 또는 acceptance criterion에 연결되어야 한다.

### 2.1 권장 순서
| Order | Workstream | Maps to SPEC | Why this comes now | Depends on |
| --- | --- | --- | --- | --- |
| 1 |  |  |  |  |
| 2 |  |  |  |  |
| 3 |  |  |  |  |

### 2.2 Workstream 상세

#### Workstream: [Name]
- Goal:
- Maps to SPEC:
- Main changes:
- Key files / areas likely affected:
- Dependency / integration points:
- Done when:
- Verification:

#### Workstream: [Name]
- Goal:
- Maps to SPEC:
- Main changes:
- Key files / areas likely affected:
- Dependency / integration points:
- Done when:
- Verification:

### 2.3 병렬화와 병합 지점
- Safe parallel work:
- Shared integration points / likely conflict areas:
- Final integration checkpoint:

---

## 3. 검증 및 테스트 전략 (Validation and Testing Strategy)

> 테스트는 비즈니스 정합성을 증명하고 delivery risk를 줄여야 한다.
> 테스트 개수나 넓지만 얕은 coverage를 목표로 하지 않는다.
> 사소한 assertion 여러 개보다 가치 높은 테스트 몇 개를 우선한다.
> Meaningful verification = 약속한 동작, 계약, 또는 operator-visible outcome이 실제로 성립함을 보여주는 관찰 가능한 증거다.

### 3.1 리스크 기반 테스트 초점
가장 위험한 동작부터 적는다.

| Spec ref | Risk / business rule | Why it matters | Best test level | Planned proof |
| --- | --- | --- | --- | --- |
|  |  |  |  |  |
|  |  |  |  |  |

가이드:
- business rule, state transition, authorization boundary, data integrity, failure recovery를 우선 검증한다.
- SPEC의 각 critical business rule, invariant, auth rule, failure path마다 planned proof를 적거나, 왜 자동화를 추가하지 않는지 정당화한다.
- correctness가 persistence, transaction boundary, framework wiring, external-contract mapping에 의존하면 integration test를 추가한다.
- domain logic에 의미 있는 branching이나 invariant가 있다면 unit test를 추가한다.
- framework 기본 동작, getter/setter, trivial DTO shape 반복, 의미 없는 one-assertion wrapper를 검증하는 테스트는 넣지 않는다.
- 위험도가 진짜 높지 않다면 모든 invalid permutation을 exhaustive하게 테스트하지 않는다.

### 3.2 계획된 자동화 테스트
| Spec ref / acceptance criterion | Scenario / rule | Test level | Why this level | Observable proof |
| --- | --- | --- | --- | --- |
|  |  | Unit / Integration / E2E-smoke |  |  |
|  |  | Unit / Integration / E2E-smoke |  |  |

### 3.3 자동화 테스트로 다루지 않는 항목
| Spec ref / rule | Why not automated | Manual / operational proof |
| --- | --- | --- |
|  |  |  |

### 3.4 테스트 환경과 double
- DB / storage / broker setup:
- External dependency isolation approach:
- Time / async / retry control approach:
- Required fixtures or seed data:

### 3.5 검증 명령과 quality gate
- Required commands:
- Minimum meaningful checks before merge:
- Evidence to attach (test output, screenshots, logs, migration proof, etc.):

---

## 4. 전달 리스크와 안전장치 (Delivery Risks and Safeguards)

| Risk | Impact | Mitigation | Verification |
| --- | --- | --- | --- |
|  |  |  |  |

다음 같은 항목을 포함한다:
- schema 또는 contract drift
- partial failure로 인한 inconsistent state
- idempotency gap
- rollout compatibility issue
- observability blind spot

---

## 5. Rollout and Rollback

### 5.1 Rollout plan
- Migration / schema steps:
- Config / secret / infra changes:
- Backward / forward compatibility considerations:
- Monitoring signals to watch during rollout:
- Post-deploy checks:

### 5.2 Rollback plan
- App rollback:
- Data rollback or safe-forward plan:
- Async / message compatibility fallback:
- Partial deployment recovery:

---

## 6. 완료 체크리스트 (Completion Checklist)

- [ ] 모든 계획된 workstream이 target SPEC에 매핑된다.
- [ ] 계획된 테스트가 SPEC section 또는 acceptance criteria에 매핑된다.
- [ ] 최고 위험도의 business rule에 대해 명시적 자동화 검증 또는 문서화된 예외 사유가 있다.
- [ ] 저가치 또는 중복 테스트를 의도적으로 피했다.
- [ ] 필요한 observability와 failure-path 점검이 포함되어 있다.
- [ ] rollout / rollback 단계에 compatibility 가정과 monitoring signal이 포함되어 있다.
- [ ] 남아 있는 open question 또는 deferred item이 기록되어 있다.
