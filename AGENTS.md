## 응답 가독성 지침

에이전트는 답변을 작성할 때 "정확하지만 쉽게 읽히는 설명"을 우선한다.

금지:
- 불필요한 영어 혼용
- 긴 복문
- 의미를 흐리는 추상어 남발
- 설명 없이 전문 용어만 나열
- 있어 보이지만 실제 이해를 늦추는 표현

권장:
- 짧은 문장
- 쉬운 한국어
- 필요한 용어만 사용
- 용어 사용 시 한 줄 설명 추가
- 예시 중심 설명
- 결론 → 이유 → 예시 → 적용 순서

정확성:

- 코드를 기반으로 응답시 codebase-memory-mcp 활용 실제 작성된 코드를 확인하고 응답

기준:
사용자가 답변을 읽고 "그래서 내가 뭘 해야 하는지"를 바로 알 수 있어야 한다.

## Project Rules

- Before adding new logic, check whether the same behavior already exists elsewhere and extend or reuse it instead of duplicating it.
- Do not leave the same responsibility implemented in two places unless the duplication is temporary and explicitly documented.
- 특히 현재 브랜치에 없는 맥락이나 선행 설계 결정을 확인할 때는 동일 책임이 이미 그 브랜치에서 어떻게 정리되었는지 먼저 보고, 필요한 부분만 재사용하거나 일관되게 확장한다.
- 코드 수정시 # 또는 ''' 내용 '''으로 표기한 주석 수정 금지: 만약 변경된 코드와 주석이 불일치하면 주석 내용을 변경된 코드에 맞게 수정

## 질문 응답, 문서 작성 가이드
/home/artyom9/project/Biblio/docs/prompts/plain_response_guidelines.md
참고 해서 질문 응답
기술용어,라이브러리,모듈명 과 같이 임의로 변경되어서는 절대 안되는 용어를 제외하곤 전부 plain한 어조 유지, jargon 사용금지

## Design Document Guardrails

- When creating or revising architecture, system design, spec, or plan documents, read and follow `docs/prompts/design_doc_guardrails.md` before writing.
- Apply that guide again before finalizing the document.
- Keep the document at the abstraction level required by its document type. Do not let system design drift into spec-level implementation detail.

## Python / FastAPI / Sonar Rules
When modifying Python, FastAPI, or tests, read and follow:
`docs/prompts/python_fastapi_sonar_rules.md`

## 코드 작성 가이드

- 함수는 한 가지 책임만 갖도록 작성한다. 특별한 이유가 없다면 하나의 함수가 50줄을 넘지 않게 분리한다.
- 함수명, 변수명, 클래스명은 역할과 의도가 드러나게 작성한다. `data`, `temp`, `process`, `handle` 같은 모호한 이름은 피한다.
- 중복 로직은 복사하지 말고 함수, 클래스, 상수 등 의미 있는 단위로 분리한다.
- 수정 영향 범위가 작아지도록 작성한다. 새 기능 추가나 정책 변경 시 기존 코드를 불필요하게 많이 고치지 않게 한다.
- 비즈니스 규칙, 매직 넘버, 매직 문자열은 코드 곳곳에 흩뿌리지 말고 한 곳에 모은다.
- 외부 API, DB, 파일 시스템 같은 외부 의존성은 핵심 로직과 섞지 말고 경계를 분리한다.
- 복잡한 조건문은 의미 있는 함수나 변수로 분리해 의도를 드러낸다.
- 예외는 조용히 무시하지 말고, 원인을 파악할 수 있게 명확히 처리한다.
- 테스트하기 쉬운 구조를 우선한다. 입력과 출력이 명확하고, 사이드 이펙트가 적은 코드를 선호한다.

## 작업 로그 규칙

사용자가 "작업 로그 남겨라" 또는 같은 의미의 요청을 하면, 아래 위치에 로컬 메모리 마크다운 파일로 작업 로그를 남긴다.

- 저장 위치: `/home/artyom9/project/agent_memory/biblio_work_log/`
- 파일명: `YYYY-MM-DD-주제-한줄요약.md` (예: `2026-06-15-gcp-배포-vm-설정.md`)
- 양식에 맞춰서 작성:`/home/artyom9/project/agent_memory/biblio_work_log/개발로그양식.md`
- 같은 날 같은 주제로 이어지는 작업이면 새 파일을 만들지 말고 기존 파일에 이어서 기록한다.



<!-- ## Prism / Glint Review System

When asked to review a design, spec, or code artifact:

- System design docs: $prism-design or $glint-design
- Spec / plan docs:   $prism-spec   or $glint-spec
- Source code / diff: $prism-code   or $glint-code

Prism = senior panel review (L1 architect + L2 SRE + L3 ML platform + L4 staff).
Glint = fast hygiene / presence check (skips substance).

Shared criteria (read-only SOT): prompts/review_criteria/
Orchestration (Codex):            .agents/skills/prism-*/, .agents/skills/glint-*/
Lens subagents (Codex):           .codex/agents/prism-lens-*.toml
Outputs (prism only):             docs/reviews/<artifact-slug>/YYYY-MM-DD-HHmm.md -->
