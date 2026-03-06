---
name: doc-revise
description: "기존 Tech Spec 문서를 수정할 때 SOT 정합성, 임의 판단 방지, 문서 간 모순 방지를 보장하는 워크플로우"
disable-model-invocation: true
argument-hint: "[컴포넌트명] [변경 내용 요약]"
---

# Tech Spec 문서 수정 워크플로우

기존 문서를 수정하면서 system-design SOT 정합성, 임의 판단 방지, 문서 간 모순을 방지한다.

- 대상 컴포넌트: **$ARGUMENTS[0]**
- 변경 요청: **$ARGUMENTS[1]**

---

## 참조 파일

| 파일 | 역할 |
|------|------|
| `docs/system-design.md` | **최우선 SOT** — 여기에 없는 내용은 추가 불가 |
| `docs/PRD.md` | 후순위 참조 |
| `.claude/skills/doc-revise/revision-guidelines.md` | 수정 지침 |
| `.claude/skills/doc-revise/revision-review-criteria.md` | 검증 기준 |

수정 대상 문서:
- `docs/Tech_Spec/$ARGUMENTS[0]_Spec.md`
- `docs/Tech_Spec/$ARGUMENTS[0]_Plan.md`

교차 검증 대상:
- `docs/Tech_Spec/` 내 모든 기존 `*_Spec.md`, `*_Plan.md`

---

## 워크플로우 실행 절차

### Step 1: SOT 정합성 검사 (Gate Keeper 서브 에이전트)

Task 도구로 `general-purpose` 서브 에이전트를 실행한다.

**Gate Keeper 프롬프트에 반드시 포함할 내용:**

1. `docs/system-design.md`를 **먼저 읽을 것**
2. 변경 요청: "$ARGUMENTS[1]"
3. 아래 세 가지를 판정할 것:

**판정 A: 근거 존재 여부**
- 변경 요청의 내용이 system-design.md에 근거가 있는가?
- 근거가 **없으면**: `REJECT` — 이유와 함께 즉시 중단. 사용자에게 "system-design에 근거가 없으므로 먼저 system-design 수정이 필요합니다"를 보고
- 근거가 **있으면**: 관련 섹션을 정확히 인용하여 다음 단계로 전달

**판정 B: 정보 충분성**
- 변경을 실행하기 위한 설계 정보가 system-design에 충분한가?
- 부족하면: 부족한 항목을 `[USER_INPUT_REQUIRED: 질문]` 형태로 목록화

**판정 C: 수정 범위 식별**
- 대상 파일의 어느 섹션이 수정되어야 하는가?
- 수정 범위를 섹션 번호와 제목으로 명시

**출력 형식:**
```
## Gate Keeper 결과
- 판정: PASS / REJECT
- SOT 근거: (system-design.md에서 인용)
- 정보 부족 항목: (있을 경우)
- 수정 대상 섹션: (섹션 번호 + 제목 목록)
```

**REJECT이면 여기서 워크플로우 중단.** 사용자에게 보고 후 종료한다.

### Step 2: 영향 범위 분석 (Impact Analyzer 서브 에이전트)

Gate Keeper가 PASS한 경우에만 실행한다.

**Impact Analyzer 프롬프트에 반드시 포함할 내용:**

1. Gate Keeper 결과 전문을 전달
2. `docs/Tech_Spec/` 내 **모든** 기존 Spec/Plan 문서를 읽을 것
3. 변경이 영향을 미치는 파일과 지점을 분류할 것:

| 분류 | 설명 | 예시 |
|------|------|------|
| 직접 수정 | 변경 요청의 대상 파일 | `Core_Api_Server_Spec.md §2.1` |
| 연쇄 수정 | 공유 계약(API, 메시지, 스키마)이 변경되어 함께 수정해야 하는 파일 | `Pipeline_Worker_Spec.md §2.1 메시지 계약` |
| 주의 확인 | 직접 수정은 불요하나 모순 가능성을 검증해야 하는 파일 | `Search_Service_Plan.md §1.1 의존성` |

**출력 형식:**
```
## 영향 범위 분석 결과
### 직접 수정
- [파일명] §[섹션]: [이유]

### 연쇄 수정
- [파일명] §[섹션]: [이유]

### 주의 확인
- [파일명] §[섹션]: [이유]
```

### Step 3: USER_INPUT_REQUIRED 확인

Step 1에서 정보 부족 항목이 있었으면 사용자에게 질문을 모아서 전달한다.
답변을 받은 후 Step 4로 진행한다.

### Step 4: 수정 실행 (Writer 서브 에이전트)

**Writer 프롬프트에 반드시 포함할 내용:**

1. `.claude/skills/doc-revise/revision-guidelines.md`를 **먼저 읽을 것**
2. Gate Keeper 결과 전문 (SOT 근거 포함)
3. 영향 범위 분석 결과 전문
4. 사용자 답변 (있을 경우)
5. 아래 제약을 반드시 준수할 것:
   - **Gate Keeper가 인용한 system-design 근거에 있는 내용만 작성**
   - **수정 범위 밖의 섹션은 건드리지 않을 것**
   - **연쇄 수정 대상 파일도 함께 수정할 것**
   - 정보가 부족한 항목은 `[USER_INPUT_REQUIRED: 질문]` 처리
6. 수정 완료 후 변경된 내용을 diff 형태로 요약할 것

### Step 5: 교차 일관성 검증 (Reviewer 서브 에이전트)

**Reviewer 프롬프트에 반드시 포함할 내용:**

1. `.claude/skills/doc-revise/revision-review-criteria.md`를 **먼저 읽을 것**
2. Gate Keeper 결과 (SOT 근거)
3. 영향 범위 분석 결과
4. Writer의 변경 요약 (diff)
5. 검증 대상: 수정된 파일 + 영향 범위의 "주의 확인" 파일
6. 피드백을 **구체적인 수정 지시** 형태로 출력
7. 모든 기준 통과 시 "PASS" 명시

### Step 6: 피드백 반영 재수정 (Writer 서브 에이전트)

Reviewer가 PASS하지 않은 경우:
1. `revision-guidelines.md`를 다시 읽을 것
2. 피드백 내용 전문 전달
3. 피드백 항목을 하나씩 반영
4. 수정 시에도 SOT 근거 범위 밖의 내용 추가 금지

### Step 7: 2차 검증 (Reviewer 서브 에이전트)

Step 5와 동일한 방식으로 재검증.
추가로 **1차 피드백 항목이 올바르게 반영되었는지** 확인한다.

### Step 8: 최종 보고

사용자에게 다음을 보고한다:

1. **SOT 근거 요약** — system-design.md의 어느 부분에 근거하여 수정했는지
2. **수정된 파일 목록 및 변경 요약**
   - 직접 수정 파일: 변경 내용
   - 연쇄 수정 파일: 변경 내용
3. **피드백 사이클 요약** — 지적 사항과 반영 결과
4. **잔여 이슈** (있을 경우)
5. **추가 피드백 사이클 필요 여부** 의견 제시

사용자가 추가 사이클을 요청하면 Step 5~7을 반복한다.

---

## 주의사항

- system-design.md에 근거가 없는 변경은 **Gate Keeper 단계에서 차단**된다
- system-design.md 자체를 수정해야 하는 경우, 이 skill이 아니라 사용자가 직접 system-design을 먼저 수정한 후 이 skill을 실행해야 한다
- 연쇄 수정이 다수 파일에 걸칠 경우, Writer가 모든 파일을 한 번에 수정한다
- 피드백 사이클 횟수 변경은 반드시 사용자 합의 후 진행한다
