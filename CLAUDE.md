## Project Rules

- Before adding new logic, check whether the same behavior already exists elsewhere and extend or reuse it instead of duplicating it.
- Do not leave the same responsibility implemented in two places unless the duplication is temporary and explicitly documented.
- 피드백 수집 파이프라인 관련 코드, Spec, ADR, Plan 문서를 확인해야 하면 먼저 로컬 브랜치 `feat/74-feedback-ingestion-pipeline`의 구현과 문서를 참고한다.
- 특히 현재 브랜치에 없는 맥락이나 선행 설계 결정을 확인할 때는 동일 책임이 이미 그 브랜치에서 어떻게 정리되었는지 먼저 보고, 필요한 부분만 재사용하거나 일관되게 확장한다.
- 코드 수정시 #으로 표기한 주석 수정 금지: 만약 변경된 코드와 주석이 불일치하면 주석 내용을 변경된 코드에 맞게 수정

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


## Prism / Glint Review System

When asked to review a design, spec, or code artifact:

- System design docs: $prism-design or $glint-design
- Spec / plan docs:   $prism-spec   or $glint-spec
- Source code / diff: $prism-code   or $glint-code

Prism = senior panel review (L1 architect + L2 SRE + L3 ML platform + L4 staff).
Glint = fast hygiene / presence check (skips substance).

Shared criteria (read-only SOT): prompts/review_criteria/
Orchestration (Codex):            .agents/skills/prism-*/, .agents/skills/glint-*/
Lens subagents (Codex):           .codex/agents/prism-lens-*.toml
Outputs (prism only):             docs/reviews/<artifact-slug>/YYYY-MM-DD-HHmm.md