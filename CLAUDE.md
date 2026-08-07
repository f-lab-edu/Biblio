<!-- mr-bae:start -->
<!-- SOT: /home/artyom9/project/mr-bae/references/always-on-guidance.md -->
## 기본 가독성 규칙

- 사용자의 말버릇을 흉내 내지 말고 이해 순서에 맞춰 쓴다.
- 질문의 직접 답이나 결론을 먼저 말한다.
- 사실·추정·제안을 구분하고, 근거 없는 수치나 정책을 확정하지 않는다.
- 자체 조어, 추상적인 비유, 부정형 서두를 피한다.
- 짧게 쓰되 원인과 결론 사이의 핵심 연결은 생략하지 않는다.
- 사용자 판단의 근거가 약하면 동조하지 말고 확인되지 않은 부분을 구체적으로 말한다.
- 결과 문서에 에이전트의 작업 과정, 실수 회고, 대화 이력을 넣지 않는다.
- 사용자가 `Mr.Bae` 스킬을 명시적으로 호출하면 상세 작성 절차를 적용한다.
<!-- mr-bae:end -->

### 이 저장소에서 추가로 지킬 점

- 코드를 기반으로 응답할 때는 codebase-memory-mcp로 실제 작성된 코드를 확인한다. 인덱스 이름은 `home-artyom9-project-Biblio`다.
- 사용자가 답변을 읽고 무엇을 해야 하는지 바로 알 수 있어야 한다.

## Project Rules

- Before adding new logic, check whether the same behavior already exists elsewhere and extend or reuse it instead of duplicating it.
- Do not leave the same responsibility implemented in two places unless the duplication is temporary and explicitly documented.
- 특히 현재 브랜치에 없는 맥락이나 선행 설계 결정을 확인할 때는 동일 책임이 이미 그 브랜치에서 어떻게 정리되었는지 먼저 보고, 필요한 부분만 재사용하거나 일관되게 확장한다.
- 코드 수정시 # 또는 ''' 내용 '''으로 표기한 주석 수정 금지: 만약 변경된 코드와 주석이 불일치하면 주석 내용을 변경된 코드에 맞게 수정

## 질문 응답, 문서 작성 가이드
/home/artyom9/project/Biblio/docs/prompts/plain_response_guidelines.md
참고 해서 질문 응답
기술용어,라이브러리,모듈명 과 같이 임의로 변경되어서는 절대 안되는 용어를 제외하곤 전부 plain한 어조 유지, jargon 사용금지

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

로그 규칙의 SOT는 `/home/artyom9/project/agent_memory/core/logging-core.md`다.
- "작업 로그 남겨라", "마감 정산" 등 기록 요청 시 그 문서의 절차를 따른다.
- 하루 마감 정산은 logday 스킬, 세션 조각은 log-fragment 스킬로 실행한다.
