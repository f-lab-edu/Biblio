---
name: doc-write
description: "Tech Spec(SPEC.md, PLAN.md) 문서를 작성하고 자동 피드백 사이클을 거쳐 정제된 결과물을 출력하는 워크플로우"
disable-model-invocation: true
argument-hint: "[컴포넌트명]"
---

# Tech Spec 문서 작성 워크플로우

사용자가 지정한 **하나의 컴포넌트**에 대해 SPEC.md와 PLAN.md를 작성하고,
자동 피드백 사이클을 거쳐 정제된 결과물을 출력한다.

대상 컴포넌트: **$ARGUMENTS**

---

## 참조 파일 (반드시 확인)

| 파일 | 역할 |
|------|------|
| `docs/system-design.md` | **최우선 SOT** |
| `docs/PRD.md` | 후순위 참조 (모순 시 system-design 우선) |
| `docs/Tech_Spec/spec_template.md` | SPEC 양식 |
| `docs/Tech_Spec/plan_template.md` | PLAN 양식 |
| `.claude/skills/doc-write/writing-guidelines.md` | 작성 지침 |
| `.claude/skills/doc-write/review-criteria.md` | 피드백 기준 |

이미 작성된 다른 컴포넌트 문서들 (충돌 확인용):
- `docs/Tech_Spec/` 내 기존 `*_Spec.md`, `*_Plan.md` 파일들

---

## 워크플로우 실행 절차

### Step 1: 초안 작성 (Writer 서브 에이전트)

Task 도구로 `general-purpose` 서브 에이전트를 실행한다.

**Writer 서브 에이전트 프롬프트에 반드시 포함할 내용:**

1. `.claude/skills/doc-write/writing-guidelines.md`를 **먼저 읽고** 지침을 숙지한 뒤 작성할 것
2. `docs/system-design.md`를 최우선 SOT로 삼을 것
3. `docs/Tech_Spec/spec_template.md`, `docs/Tech_Spec/plan_template.md` 양식을 따를 것
4. 대상 컴포넌트: `$ARGUMENTS`
5. **충분한 정보가 없는 항목은 절대 임의로 채우지 말고 `[USER_INPUT_REQUIRED: 질문 내용]` 플레이스홀더를 남길 것**
6. 이미 작성된 다른 컴포넌트 Spec/Plan 문서들(`docs/Tech_Spec/` 내)을 읽고 용어, 기술스택, 변수명의 일관성을 유지할 것
7. 결과물을 `docs/Tech_Spec/$ARGUMENTS_Spec.md`와 `docs/Tech_Spec/$ARGUMENTS_Plan.md`에 작성할 것

### Step 2: USER_INPUT_REQUIRED 확인

Writer 서브 에이전트 완료 후, 작성된 문서에서 `[USER_INPUT_REQUIRED` 패턴을 검색한다.
- 존재하면: 사용자에게 해당 질문들을 모아서 전달하고, 답변을 받은 후 Writer 서브 에이전트를 재실행하여 반영한다.
- 존재하지 않으면: Step 3으로 진행한다.

### Step 3: 1차 피드백 (Reviewer 서브 에이전트)

Task 도구로 `general-purpose` 서브 에이전트를 실행한다.

**Reviewer 서브 에이전트 프롬프트에 반드시 포함할 내용:**

1. `.claude/skills/doc-write/review-criteria.md`를 **먼저 읽고** 기준을 숙지한 뒤 피드백할 것
2. `docs/system-design.md`를 기준으로 모순 검증 (최우선)
3. `docs/Tech_Spec/` 내 다른 컴포넌트 문서들과의 충돌 확인
4. 검토 대상: `docs/Tech_Spec/$ARGUMENTS_Spec.md`, `docs/Tech_Spec/$ARGUMENTS_Plan.md`
5. 피드백을 **구체적인 수정 지시** 형태로 출력할 것 (예: "섹션 2.1의 X를 Y로 변경")
6. 문제가 없으면 "PASS"를 명시할 것

### Step 4: 피드백 반영 재작성 (Writer 서브 에이전트)

1차 피드백 결과를 Writer 서브 에이전트에 전달하여 문서를 수정한다.

**프롬프트에 포함할 내용:**
1. `.claude/skills/doc-write/writing-guidelines.md`를 다시 읽을 것
2. 1차 피드백 내용 전문을 전달
3. 피드백 항목을 하나씩 반영하여 문서를 수정할 것
4. 수정 시에도 임의 판단 금지 원칙 유지

### Step 5: 2차 피드백 (Reviewer 서브 에이전트)

Step 3과 동일한 방식으로 재검토한다.
추가로 **1차 피드백 항목이 올바르게 반영되었는지** 확인한다.

### Step 6: 최종 보고

사용자에게 다음을 보고한다:

1. **작성된 파일 경로**
2. **피드백 사이클 요약** (1차/2차 피드백에서 지적된 사항과 반영 결과)
3. **잔여 이슈** (있을 경우)
4. **추가 피드백 사이클 필요 여부** 의견 제시

사용자가 추가 사이클을 요청하면 Step 3~5를 반복한다.

---

## 주의사항

- 한 번에 **하나의 컴포넌트만** 작성한다
- 서브 에이전트가 사용자에게 직접 질문할 수 없으므로, `[USER_INPUT_REQUIRED]` 플레이스홀더로 처리한다
- 피드백 사이클 횟수 변경은 반드시 사용자 합의 후 진행한다
