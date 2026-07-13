# Git Naming Convention

`Biblio` 프로젝트에서 사용하는 브랜치, 커밋, PR 네이밍 규칙을 정의한다.

## 목적

- 작업 단위를 GitHub Issue 기준으로 일관되게 추적한다.
- 브랜치, 커밋, PR 제목만 보고 변경 목적을 빠르게 파악한다.
- Git 로그와 PR 목록을 사람이 읽기 쉽게 유지한다.

## 기본 원칙

- 작업 식별자는 `GitHub Issue 번호`를 사용한다.
- 브랜치와 PR 제목에는 Issue 번호를 반드시 포함한다.
- 커밋 메시지는 `Conventional Commits` 형식을 사용한다.
- 커밋 메시지의 Issue 번호 포함은 선택 사항으로 한다.
- 커밋 메세지는 별도 요청이 없으면 기본값은 한국어로 작성
- 기본 머지 방식은 `Squash Merge`를 권장한다.

## Type 규칙

다음 타입만 사용한다.

| Type | 의미 | 사용 예시 |
| --- | --- | --- |
| `feat` | 사용자 관점의 새로운 기능 추가 | API 추가, 검색 기능 추가 |
| `fix` | 기존 동작의 버그 수정 | 예외 처리 보완, 상태 전이 수정 |
| `refactor` | 동작 변경 없는 내부 구조 개선 | 함수 분리, 책임 재구성 |
| `docs` | 문서만 수정 | README, ADR, Spec, Runbook 수정 |
| `test` | 테스트 코드만 추가 또는 수정 | unit/integration test 보강 |
| `chore` | 설정, 의존성, CI, 스크립트 등 잡무성 변경 | lint 설정, dependency update |
| `hotfix` | 운영 이슈 대응을 위한 긴급 수정 | 장애 대응 패치 |

## 브랜치 네이밍

형식:

```text
<type>/<issue-number>-<short-slug>
```

규칙:

- `type`은 위 표의 값 중 하나를 사용한다.
- `issue-number`는 GitHub Issue 번호만 넣는다.
- `short-slug`는 영어 소문자와 하이픈(`-`)만 사용한다.
- `short-slug`는 구현 상세보다 작업 목적을 드러내도록 작성한다.

예시:

```text
feat/123-video-upload
fix/241-search-timeout
refactor/198-preprocess-orchestrator
docs/310-git-naming-convention
test/322-upload-complete-idempotency
chore/415-pre-commit-hooks
hotfix/501-duplicate-callback-guard
```

## 커밋 메시지 네이밍

형식:

```text
<type>(<scope>): <summary>
```

권장 형식:

```text
<type>(<scope>): <summary> (#<issue-number>)
```

규칙:

- `type`은 위 표의 값 중 하나를 사용한다.
- `scope`는 변경이 일어난 서비스 또는 모듈을 나타낸다.
- `summary`는 명령형 현재 시제로 간결하게 작성한다.
- 첫 글자는 소문자로 시작한다.
- 마침표는 붙이지 않는다.
- 하나의 커밋에는 하나의 의도만 담는다.

추천 scope 예시:

- `core-api`
- `search-service`
- `pipeline-worker`
- `managed-embedding-endpoint`
- `repo`
- `docs`

예시:

```text
feat(core-api): add upload completion endpoint (#123)
fix(search-service): handle empty retrieval result (#241)
refactor(pipeline-worker): split preprocess orchestrator
docs(repo): add git naming convention
test(core-api): add upload completion idempotency cases
chore(repo): add commitlint config
hotfix(core-api): reject duplicate callback request (#501)
```

## PR 제목 네이밍

형식:

```text
<type>(<scope>): <summary> [#<issue-number>]
```

규칙:

- 브랜치와 동일한 작업 목적을 유지한다.
- PR 제목만 읽어도 변경 목적이 드러나야 한다.
- PR 제목에는 반드시 GitHub Issue 번호를 포함한다.

예시:

```text
feat(core-api): add video upload initiation API [#123]
fix(search-service): prevent timeout on empty query [#241]
refactor(pipeline-worker): split preprocess workflow [#198]
docs(repo): define git naming convention [#310]
```

## PR 본문 규칙

PR 본문에는 아래 중 하나를 반드시 포함한다.

- `Closes #123`
- `Fixes #123`
- `Refs #123`

권장 예시:

```markdown
## Summary
- add upload completion endpoint
- validate object existence before status transition

## Issue
Closes #123
```

## 예외 규칙

- 아주 작은 저장소 관리 작업은 `no-ticket` 브랜치를 허용할 수 있다.
- `no-ticket`은 문서 오탈자 수정, 로컬 개발 편의 스크립트 정리 등 추적 가치가 낮은 작업에만 사용한다.
- `no-ticket` 사용이 잦아지면 Issue를 먼저 생성하는 것을 기본 원칙으로 되돌린다.

예시:

```text
docs/no-ticket-readme-typo
chore/no-ticket-local-dev-alias
```

## 운영 권장사항

- 하나의 PR은 가능한 한 하나의 Issue만 다룬다.
- 큰 작업은 먼저 Issue로 쪼개고, 브랜치도 그 단위에 맞춰 나눈다.
- `chore`는 남용하지 않는다. 기능이면 `feat`, 버그면 `fix`, 구조 개선이면 `refactor`를 우선 사용한다.
- 운영 장애 대응이 아니면 `hotfix` 대신 일반 `fix`를 사용한다.

## 빠른 예시

Issue:

```text
#123 Add video upload initiation API
```

브랜치:

```text
feat/123-video-upload-initiation
```

커밋:

```text
feat(core-api): add upload initiation endpoint (#123)
```

PR:

```text
feat(core-api): add video upload initiation API [#123]
```
