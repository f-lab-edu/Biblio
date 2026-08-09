# Biblio System Design

- 기준일: 2026-08-05
- 기준 브랜치: `feat/104-search-load-test-baseline`
- 이 문서는 **현재 저장소의 코드와 Terraform에 선언된 상태**를 기술한다. 앞으로의 계획이나 목표 구조가 아니다.
- 상위 SOT는 `docs/PRD.md`, 하위 상세는 `docs/Tech_Spec/`와 `docs/ADR/`에 있다.
- 본문 1~6장은 논리 구조를 기술한다. 실제 제품·인프라 이름은 7장 배포 구조에 모아 두었다.

---

# 1. 시스템 개요

Biblio는 사용자가 프로젝트 단위로 영상을 모아 두고, 그 프로젝트 안에서 자연어로 질문하면 근거 구간(타임스탬프)과 함께 답변을 받는 서비스다.

처리는 두 갈래로 나뉜다.

- **업로드 경로(비동기)**: 영상을 받아 오디오·키프레임을 뽑고, 음성을 텍스트로 바꾸고, 의미 단위로 잘라 임베딩한 뒤 검색 가능 상태로 만든다.
- **검색 경로(동기)**: 질의를 임베딩해 키워드 검색과 벡터 검색을 동시에 돌리고, 두 순위를 합친 뒤 원본 DB로 다시 검증하고, 통과한 청크만 LLM 프롬프트에 넣어 답변을 만든다.

여기에 운영 사이클이 하나 더 붙는다. 검색 결과에 대한 좋아요/싫어요를 원본 로그로 쌓고, 그 로그로 학습 데이터셋을 만들어 임베딩 모델을 재학습·평가하고, 통과하면 서빙 모델을 교체한다. 교체한 모델이 문제가 있으면 마지막 정상 상태로 되돌린다.

## 1.1 설계상 지켜지는 원칙

1. **Metadata DB가 유일한 SOT다.** 벡터 인덱스는 그로부터 파생된 사본이며, 사용자에게 노출되기 전에 항상 Metadata DB로 다시 검증한다.
2. **검색 범위는 인증된 사용자와 그가 소유한 프로젝트 하나의 교집합이다.** 이 조건은 후보 생성 SQL과 최종 검증 SQL 양쪽에 모두 들어간다.
3. **무거운 작업은 API 요청 안에서 하지 않는다.** 큐에 넣고 즉시 접수 응답을 준다.
4. **모델 서빙 상태는 `model_release` 레코드 한 줄이 결정한다.** 배포 설정값은 이 레코드가 없을 때의 부트스트랩 기본값일 뿐이다.
5. **상태 전이는 DB 상태 컬럼으로 통제한다.** 워커는 각 단계 진입 전에 상태를 다시 읽어 중단·재개·삭제를 판단한다.

---

# 2. 컴포넌트

## 2.1 Frontend (Web UI 겸 프록시)

브라우저에서 실행되는 화면과, 브라우저 요청을 백엔드로 넘기는 서버 측 프록시를 같은 배포 단위가 함께 담당한다. 시스템의 유일한 공개 진입점이다.

**화면 기능**

1. 회원가입·로그인, 프로젝트 목록과 프로젝트 상세.
2. 프로젝트 안에서 로컬 파일 업로드 또는 외부 URL 추가, 제목 수정, 삭제.
3. 영상 처리 상태와 실패 사유 표시. 처리 중인 항목은 주기적으로 다시 조회한다.
4. 프로젝트 범위 검색과 답변·근거 구간 표시, 지난 검색 기록 조회.
5. 검색 응답 단위 좋아요/싫어요 입력.
6. 재생: 로컬 파일은 Core API에서 재생용 서명 URL을 받아 재생하고, 외부 URL은 백엔드를 거치지 않고 원본 링크와 타임스탬프로 직접 제어한다.

**프록시 기능**

- `/api/v1/**` 요청을 받아 첫 경로 조각이 `search`면 Search Service로, 나머지는 Core API로 보낸다.
- 원본 헤더와 본문을 그대로 전달하고, 백엔드 호출 인증 토큰만 추가한다. 사용자 인증 정보는 손대지 않고 그대로 넘긴다.

## 2.2 Core API

검색을 제외한 모든 사용자 요청을 처리하고, 공유 데이터베이스의 스키마를 소유한다.

**인증·인가**

- JWT를 직접 검증한다. `Authorization: Bearer` 헤더가 없으면 인증 쿠키를 읽고, 쿠키로 인증할 때는 CSRF 토큰(쿠키값과 `X-CSRF-Token` 헤더값 일치)을 함께 확인한다. 로그인·회원가입 경로는 CSRF 확인에서 제외된다.
- claim의 `requester_user_id`로 프로젝트 소유권을 확인한다. 접근 제어의 기준은 `project.user_id`다.
- claim의 `role`이 `ADMIN`인 경우에만 운영자 전용 경로를 허용한다.

**사용자 기능**

| 경로 | 동작 |
|---|---|
| `POST /auth/signup`, `POST /auth/login`, `POST /auth/logout`, `GET /auth/me` | 계정 생성, 로그인·로그아웃(인증 쿠키 설정·삭제), 현재 사용자 조회 |
| `POST /projects`, `GET /projects`, `PATCH /projects/{id}` | 프로젝트 생성·목록·수정 |
| `DELETE /projects/{id}` | 프로젝트를 `DELETING`으로 전이하고 `PROJECT_DELETE_REQUEST` 발행 (202) |
| `POST /videos`, `POST /projects/{id}/videos` | 영상 등록. 로컬 파일이면 업로드용 서명 URL 반환(201), 외부 URL이면 즉시 `PREPROCESS_REQUEST` 발행(202) |
| `POST /videos/{id}/complete` | 업로드 완료 신호. `PENDING`이면 `UPLOADED`로 올리고 `PREPROCESS_REQUEST` 발행(202) |
| `GET /videos`, `GET /projects/{id}/videos`, `GET /videos/{id}`, `PATCH /videos/{id}` | 영상 목록·조회·제목 수정 |
| `DELETE /videos/{id}`, `POST /videos:batch-delete` | 대상 영상을 `DELETING`으로 전이하고 `DELETE_REQUEST` 발행 (202) |
| `POST /videos/{id}/retry` | `FAILED` 상태에서만 허용. `PENDING`으로 되돌리고 실패 메타데이터를 지운 뒤 `PREPROCESS_REQUEST` 재발행 (202) |
| `POST /videos/{id}/playback-url` | 재생용 서명 URL 발급 |
| `POST /feedbacks` | 검색 응답 단위 피드백 수집 |

업로드 완료 처리에는 복구 규칙이 하나 더 있다. 이미 `PROCESSING`이나 `READY`인 영상에 완료 신호가 다시 오면 오류 대신 현재 상태를 200으로 돌려준다. 큐 발행에 실패한 업로드를 사용자가 다시 눌러 복구할 수 있게 하기 위해서다. 업로드된 파일이 상한을 넘으면 해당 영상을 삭제 요청하고 오류를 반환한다.

**운영자 기능**

| 경로 | 동작 |
|---|---|
| `POST /admin/model-release/rollback` | 현재 릴리스 상태를 검증한 뒤 `ROLLBACK_REQUEST`를 롤백 큐에 발행하고 202를 반환한다. 실제 롤백 실행은 Feedback Loop Pipeline의 롤백 워커가 맡는다 |

**메시지 발행** — `PREPROCESS_REQUEST`, `DELETE_REQUEST`, `PROJECT_DELETE_REQUEST`, `ROLLBACK_REQUEST`.

**스키마 소유** — 모든 테이블의 마이그레이션은 Core API가 관리한다. 다른 서비스는 같은 테이블을 읽거나 쓰지만 DDL을 만들지 않는다.

## 2.3 Search Service

검색 요청만 처리한다. Core API를 거치지 않고 프록시에서 바로 들어온다.

| 경로 | 동작 |
|---|---|
| `POST /api/v1/search` | 검색 및 답변 생성 |
| `GET /api/v1/search/history` | 프로젝트 단위 지난 검색 기록 조회 |
| `POST /api/v1/internal/reload-serving-targets` | 프로세스가 들고 있는 검색 대상(모델/인덱스)을 DB에서 다시 읽어 교체 |

**검색 대상 캐시** — 어떤 모델과 인덱스로 검색할지는 `model_release`의 active/previous 조합이 정한다. 이 값을 요청마다 읽지 않고 프로세스 안에 들고 있다가, 모델이 바뀔 때 내부 경로 호출로 교체한다. 릴리스 레코드가 없거나 아직 못 읽었으면 검색을 시작하지 않고 503을 낸다.

**인증** — Core API와 같은 JWT/쿠키·CSRF 규칙을 공유 라이브러리로 함께 쓴다.

**검색 실행 순서**

1. 코퍼스 준비 상태 확인. 프로젝트에 영상이 하나도 없으면 "업로드된 영상 없음" 오류, `READY`가 아닌 영상이 하나라도 있으면 "아직 검색 준비 중" 오류를 낸다.
2. 검색 대상마다 질의를 임베딩한다. active와 previous가 함께 서빙 중이면 두 요청을 동시에 보낸다.
3. 키워드 검색과 벡터 검색을 동시에 실행한다. 벡터 검색도 대상별로 동시에 실행한다.
4. 두 순위를 RRF로 합쳐 최종 후보를 만든다.
5. Metadata DB를 서빙 게이트로 다시 조회해 소유권·존재 여부·프로젝트 범위·검색 가능 상태를 통과한 청크만 남긴다.
6. 남은 청크가 없으면 LLM을 호출하지 않고 "검색 결과 없음"을 반환한다.
7. 통과한 청크로 프롬프트를 만들고 답변과 인용 번호를 받는다.
8. 검색 응답 스냅샷과 대화 기록을 저장한다. 이 저장이 실패해도 검색 응답은 그대로 반환한다.

**기본 파라미터** — 후보 Top-K 20, 최종 Top-K 5, RRF 상수 60, 스냅샷 보존 168시간. 질의는 정규화 후 2~1,000자로 제한한다.

**검색 가능 조건** — 프로젝트가 `SERVABLE`이고 `ACTIVE`이며, 그 프로젝트의 모든 영상이 `READY`여야 한다. 이 조건은 키워드 검색, 벡터 검색, 최종 게이트 세 SQL에 모두 들어간다.

## 2.4 Managed Embedding Endpoint

텍스트를 벡터로 바꾸는 일만 한다. 자체 호스팅 모델을 CPU로 추론한다.

| 경로 | 동작 |
|---|---|
| `POST /embed` | 텍스트 목록과 모델 버전을 받아 임베딩 반환 |
| `GET /health` | 준비 상태와 현재 로드된 모델 버전 목록 반환 |
| `POST /internal/reload-models` | 릴리스 레코드를 다시 읽어 모델 런타임을 교체 |

**입장 제어(admission control)** — 요청은 `X-Embedding-Workload` 헤더로 세 갈래로 나뉜다.

| 워크로드 | 보내는 쪽 | 규칙 |
|---|---|---|
| `search` | Search Service | 텍스트 1개만 허용. 동시 접수 상한이 있고, 넘으면 즉시 503 |
| `video_preprocess` | Pipeline Worker | 배치 허용. 별도 접수 상한과 대기 상한 |
| `legacy` | 헤더가 없는 요청 | 대기 없이, 빈 슬롯이 있을 때만 처리. 없으면 즉시 503 |

접수된 요청은 공유 실행 슬롯을 기다린다. 대기열에서 꺼낼 때 `search`를 먼저 꺼낸다. 다만 이미 실행 중인 추론은 중간에 뺏지 못한다. 대기 시간이 워크로드별 상한을 넘으면 503으로 끊는다.

**모델 교체** — 후보 모델을 실제로 로드하고 준비 상태를 통과한 뒤에만 릴리스 레코드를 갱신한다. 교체 중에도 기존 런타임은 유지한다. 여러 모델 버전을 동시에 들고 있을 수 있으며 `/health`가 그 목록을 알려준다.

## 2.5 Pipeline Worker

큐를 계속 폴링하는 워커다. HTTP 서버는 헬스체크용으로만 띄운다. 큐 이름은 메시지 타입 이름과 같다.

**처리 단계** — 하나의 프로세스 안에서 순서대로 진행한다.

| 단계 | 하는 일 |
|---|---|
| `DOWNLOAD` | 외부 URL이면 다운로드해 저장소에 올린다. 로컬 파일이면 저장소에서 내려받는다 |
| `EXTRACT` | 오디오를 뽑고 길이를 잰다. 이미 오디오 자산이 있으면 다시 뽑지 않는다 |
| `STT` | 오디오를 텍스트와 타임스탬프로 바꾼다. 길이가 상한을 넘으면 여러 조각으로 나눠 처리한 뒤 겹치는 구간을 합친다 |
| `CHUNKING` | 문장 단위를 지키며 의미 단위로 자르고, 각 청크 중간 지점의 키프레임을 뽑아 이미지 설명·OCR·장면 태그를 붙여 검색용 합성 텍스트를 만든다 |
| `EMBEDDING` | 합성 텍스트를 배치로 임베딩한다. 대상 모델은 릴리스 레코드의 active를 따른다 |
| `VECTOR_UPSERT` | 청크와 벡터를 트랜잭션으로 적재하고 상태를 `READY`로 올린다 |

**처리권 확보와 재개** — 처리 시작 시 처리권을 잡고, 각 단계 사이에 처리 시각을 갱신한다. 워커가 죽어 처리 시각이 오래 멈춘 항목은 다른 워커가 다시 가져간다. 재처리 시에는 이미 만들어진 오디오와 대본이 있으면 건너뛰고 그 다음부터 이어서 한다.

**삭제 감지** — 각 단계 진입 전에 상태를 확인해 `DELETING`이면 그 자리에서 멈추고 정리 모드로 넘어간다.

**실패 처리** — 예외를 실패 단계(`failed_stage`)와 실패 코드(`failure_code`)로 분류해 추적 ID와 함께 기록한다. 실패 코드는 사용자에게 보여줄 사유를 정하는 데 쓴다.

| 실패 코드 | 뜻 |
|---|---|
| `YOUTUBE_BLOCKED` | 외부 플랫폼이 다운로드를 막음 |
| `SOURCE_UNAVAILABLE` | 원본을 가져올 수 없음 |
| `SOURCE_LIMIT_EXCEEDED` | 크기 또는 길이 상한 초과 |
| `AUDIO_EXTRACTION_FAILED` | 오디오 추출·검증·업로드 실패 |
| `STT_FAILED` | 음성 인식 실패 |
| `EMBEDDING_FAILED` | 임베딩 호출 실패 |
| `INDEX_WRITE_FAILED` | 최종 적재 실패 |
| `INTERNAL_PROCESSING_ERROR` | 위에 해당하지 않는 내부 오류 |

**연쇄 삭제** — `DELETE_REQUEST`는 영상 여러 건을 한 번에 받는다. Metadata DB의 벡터 항목·청크·대본·자산·영상 레코드를 트랜잭션으로 지우고, 저장소 파일은 그 뒤에 정리한다. `PROJECT_DELETE_REQUEST`는 프로젝트에 속한 영상을 모두 지운 뒤 프로젝트 레코드를 지운다. 대상이 이미 없으면 성공으로 처리한다.

## 2.6 Feedback Ingestion Pipeline

로그 수집 도구를 설정만으로 구성한 컴포넌트다. 애플리케이션 코드가 없다.

1. HTTP로 피드백 이벤트를 받아 즉시 202를 반환한다.
2. 수집 시각과 처리 컴포넌트 정보를 붙이고 JSON을 파싱한다.
3. 필수 필드가 모두 있고 스키마 버전이 지원 범위면 정상 경로, 아니면 오류 경로로 나눈다. 오류 이벤트도 버리지 않고 별도 경로에 남긴다.
4. 스키마 버전과 수집 날짜·시각으로 나눈 경로에 객체 저장소로 적재한다. 전송 실패에 대비해 디스크 버퍼와 재시도를 둔다.

## 2.7 Feedback Loop Pipeline

모델 개선 사이클을 담당한다. 하나의 코드베이스를 실행 역할(`APP_ROLE`)로 나눠 여러 프로세스로 띄운다.

| 역할 | 하는 일 |
|---|---|
| `scheduler` | 정해진 시각에 데이터셋 생성 요청과 학습 요청을 큐에 넣는다. 함께 정체된 실행 감지, 롤백 후 복구 스캔, 배포 재시도 스캔을 주기적으로 돌린다 |
| `dataset-worker` | 피드백 원본 로그를 읽어 학습용 데이터셋 산출물을 만든다 |
| `train-release-worker` | 학습 → 평가 → 평가 결과 기록 → 후보 배포 시도까지 한 흐름으로 실행한다 |
| `rollback-worker` | 롤백 요청을 받아 마지막 정상 상태로 되돌린다 |
| `legacy-reindex-worker` | 오래된 세대 인덱스에 남은 영상을 최신 대상 인덱스로 다시 색인한다 |
| `reembedding-worker` | 롤백 복구 대상 영상을 복원된 기준으로 다시 임베딩한다 |

**실행 슬롯 규칙** — 동시에 `RUNNING`인 실행은 하나만 둔다. 대기 중인 `PENDING` 실행도 하나만 둔다. 더 최신 데이터셋으로 새 실행이 들어오면 기존 대기 실행은 `SUPERSEDED`가 된다.

**판정과 실패 구분** — 평가 지표가 기준에 못 미치면 `FAIL`, 실행 자체가 깨지면 `ERROR`로 나눠 기록한다. 어느 쪽이든 기존 서빙은 그대로 유지한다.

**후보 배포** — 후보 모델을 로드하고 준비 상태를 확인한 뒤, 오래된 세대 재색인 조건을 확인하고, 마지막 정상 조합을 스냅샷으로 남긴 다음 릴리스 레코드를 갱신한다. 마지막으로 임베딩 엔드포인트와 Search Service에 교체를 알린다. 배포 시도 횟수를 세고 상한을 넘으면 `DEPLOYMENT_BLOCKED`로 남긴다.

**롤백** — 요청에 담긴 예상 모델 버전과 전환 시각이 현재 릴리스와 다르면 오래된 요청으로 보고 아무것도 하지 않는다. 조건이 맞으면 문제 모델 기간에 만들어진 데이터가 있는 프로젝트를 검색에서 제외하고, 릴리스를 `ROLLBACK_PREPARING`으로 바꾼 뒤, 복원 대상 모델 준비와 인덱스 스냅샷 복원이 끝나면 릴리스를 복원한다. 복구가 끝난 프로젝트부터 다시 검색 범위에 넣는다.

## 2.8 데이터 저장소

**Metadata DB (SOT)**

1. 사용자·프로젝트·영상·자산·대본·청크와 각 상태를 트랜잭션으로 저장한다.
2. 청크의 검색용 합성 텍스트에 대한 키워드 검색을 제공한다.
3. 최종 서빙 검증 기준 저장소로 동작한다.
4. 모델 릴리스 상태, 실행 이력, 평가 결과, 스냅샷 등록부, 재색인 작업 목록을 저장한다.
5. 검색 응답 스냅샷을 만료 시각과 함께 단기 보존하고, 검색 대화 기록을 보존한다.
6. 벡터와 작업 큐도 같은 데이터베이스 안에 있다. 논리적으로는 분리된 저장소로 다루지만 물리적으로는 한 서버다.

**Vector Store (파생 인덱스)** — 청크 단위 임베딩 벡터를 인덱스 이름별로 저장한다. 검색 범위 필터에 필요한 최소 메타데이터를 함께 갖는다. Metadata DB에서 파생된 사본이므로 지연이나 부분 실패가 있을 수 있고, 사용자 노출 정합성은 최종 서빙 검증이 보장한다.

**Object Storage** — 원본 영상·오디오·키프레임, 피드백 원본 로그와 오류 로그, 학습·평가 데이터셋, 모델 아티팩트, 평가 상세 산출물을 저장한다. 사용자 자산 버킷과 운영 산출물 버킷은 분리한다.

**Message Broker** — 데이터베이스 기반 큐를 쓴다. 큐마다 가시성 타임아웃이 다르다. 별도 DLQ는 두지 않고, 최종 실패는 영상 레코드에 상태로 남긴 뒤 메시지를 확인 처리한다.

## 2.9 운영과 관측

- 운영 액션은 운영자 권한 토큰으로 Core API 경로를 직접 호출해 수행한다.
- 모든 서비스는 구조화 로그를 남기고 `trace_id`로 요청을 연결한다. 큐 메시지와 내부 호출도 같은 `trace_id`를 물려받는다.
- 검색은 요청 한 건마다 단계별 소요 시간을 한 줄 로그로 남긴다. 단계는 질의 임베딩(대상별), 키워드 검색, 벡터 검색(대상별), 최종 게이트, 프롬프트 구성, LLM 호출, 스냅샷 저장이다. DB 연결을 확보하는 데 걸린 시간도 따로 기록한다.
- 영상 처리는 한 건이 끝날 때 단계별 소요 시간을 한 줄로 남기고, 실패 시 실패 단계·실패 코드·외부 제공자를 함께 남긴다.
- 임베딩 엔드포인트는 요청마다 어느 워크로드였는지, 대기열 깊이와 대기 시간이 얼마였는지, 접수·거절 여부를 남긴다.
- 카운터 지표는 같은 로그 스트림에 이름과 태그를 붙여 남긴다.

---

# 3. 데이터 흐름

## 3.1 영상 등록

**입력** — 프로젝트 식별자, 로컬 파일 또는 외부 URL, 제목, 카테고리.

**처리**

1. Core API가 프로젝트 소유권을 확인한다. 프로젝트가 삭제 중이거나 롤백 복구로 제외된 상태면 거부한다.
2. 영상 식별자와 저장 경로를 미리 정하고, 메타데이터를 한 번의 트랜잭션으로 저장한다. 상태는 `PENDING`이다.
3. 로컬 파일이면 업로드용 서명 URL을 발급해 201로 반환한다. 클라이언트가 업로드를 마치고 완료 신호를 보내면 상태를 `UPLOADED`로 올리고 `PREPROCESS_REQUEST`를 발행한다.
4. 외부 URL이면 다운로드를 워커에 맡기기 위해 바로 `PREPROCESS_REQUEST`를 발행하고 202를 반환한다.

**저장 위치**

| 데이터 | 저장소 |
|---|---|
| 영상 원본 파일 | Object Storage |
| 영상 메타데이터와 상태 | Metadata DB |

## 3.2 영상 처리와 색인

**입력** — `PREPROCESS_REQUEST` 한 건, 그리고 저장소의 원본 파일 또는 외부 URL.

**처리**

1. 영상 레코드와 이미 만들어진 산출물 상태를 읽는다. 레코드가 없으면 건너뛴다. `DELETING`이면 삭제로 넘어간다. 이미 `READY`이고 현재 모델 기준 산출물이 다 있으면 건너뛴다.
2. 처리권을 잡는다. 잡지 못하면 다른 워커가 처리 중이거나 삭제 중이므로 물러난다.
3. 원본 확보 → 오디오 추출 → 음성 인식 → 청킹과 화면 정보 보강 → 임베딩 → 적재 순으로 진행한다. 단계마다 삭제 요청을 다시 확인한다.
4. 적재가 끝나면 상태를 `READY`로 올린다.
5. 적재 대상 인덱스는 릴리스 레코드의 active 하나뿐이다. 후보 모델이 준비 중이라도 새 데이터를 후보 인덱스에 이중으로 쓰지 않는다. 릴리스가 롤백 준비 중이면 적재를 시작하지 않는다.

**저장 위치**

| 데이터 | 저장소 |
|---|---|
| 오디오 파일, 키프레임 이미지 | Object Storage |
| 자산 경로, 대본 구간, 청크 텍스트와 참조, 상태 | Metadata DB |
| 청크 임베딩 벡터와 필터용 메타데이터 | Vector Store |

## 3.3 검색과 답변 생성

**입력** — 프로젝트 식별자, 자연어 질의, 인증 정보.

**처리**

1. 프록시가 요청을 Search Service로 넘긴다. 인증 헤더와 쿠키는 그대로 전달한다.
2. Search Service가 JWT를 검증하고 사용자 식별자를 꺼낸다. 쿠키 인증이면 CSRF도 확인한다.
3. 질의를 정규화하고 길이를 확인한다.
4. 프로젝트의 코퍼스 준비 상태를 확인한다. 영상이 없거나 준비되지 않은 영상이 있으면 여기서 끝낸다.
5. 검색 대상마다 질의를 임베딩한다.
6. 키워드 검색과 벡터 검색을 동시에 실행한다.
7. 두 순위를 RRF로 합쳐 최종 후보를 정한다.
8. Metadata DB를 서빙 게이트로 조회해 통과한 청크와 본문·타임스탬프를 가져온다. 0건이면 LLM을 호출하지 않는다.
9. 프롬프트를 만들고 답변과 실제 인용 번호를 받는다.
10. 검색 응답 스냅샷과 대화 기록을 저장하고 응답한다.

**출력** — 요청 식별자, 답변 본문, 그리고 청크 배열. 청크마다 답변 본문의 인라인 인용 번호와 실제 인용 여부가 들어간다.

**저장 위치**

| 데이터 | 저장소 |
|---|---|
| 검색 응답 스냅샷 (만료 시각 기반 단기 보존) | Metadata DB |
| 검색 대화 기록 (질의·답변·근거 목록) | Metadata DB |

## 3.4 처리 실패

1. 실패한 단계와 실패 코드, 그때의 추적 ID를 영상 레코드에 기록하고 상태를 `FAILED`로 바꾼다.
2. 그때까지 만들어진 중간 산출물은 지우지 않는다.
3. 사용자가 재시도를 요청하면 상태를 `PENDING`으로 되돌리고 실패 메타데이터를 지운 뒤 요청을 다시 발행한다. 워커는 보존된 산출물을 보고 안전한 지점부터 이어서 한다.
4. 실패 단계 값은 분류값이며 재개 지점과 1:1은 아니다. 예를 들어 임베딩 실패는 청크와 합성 텍스트가 따로 보존되지 않으므로 청킹부터 다시 한다.
5. 최종 실패한 메시지는 별도 실패 큐로 옮기지 않고 상태만 남긴 뒤 확인 처리한다.

## 3.5 삭제

**영상 삭제**

1. Core API가 소유권을 확인하고 대상 영상을 `DELETING`으로 바꾼다. 이 시점부터 해당 프로젝트는 준비 조건을 만족하지 못하므로 검색에서 빠진다.
2. `DELETE_REQUEST`를 발행하고 202를 반환한다. 발행에 실패하면 이전 상태로 되돌린다.
3. 파이프라인이 진행 중이면 워커가 다음 단계 진입 전에 감지해 그 자리에서 멈춘다.
4. 워커가 Metadata DB 레코드를 트랜잭션으로 지우고, 저장소 파일은 그 뒤에 정리한다.

**프로젝트 삭제** — 프로젝트를 `DELETING`으로 바꾸고 `PROJECT_DELETE_REQUEST`를 발행한다. 워커가 프로젝트에 속한 영상을 모두 지운 뒤 프로젝트 레코드를 지운다.

이미 수집된 피드백 이벤트와 이미 만들어진 데이터셋은 운영 기록으로 남긴다. 다만 새 데이터셋을 만들 때 삭제된 영상이나 청크는 다시 쓰지 않는다.

## 3.6 피드백 수집

1. 사용자가 검색 응답 단위로 좋아요/싫어요를 누른다.
2. Core API가 요청 식별자에 해당하는 검색 응답 스냅샷을 찾는다. 없거나, 다른 사용자의 것이거나, 만료됐으면 거절한다.
3. 스냅샷의 질의·최종 청크·실제 인용 청크·활성 모델과 인덱스·조회 경로를 이벤트에 함께 담는다. 이벤트 식별자는 사용자·요청·평가값으로 결정적으로 만든다. 같은 사람이 같은 응답에 같은 평가를 반복해도 같은 식별자가 나온다.
4. 수집 파이프라인으로 전달한다. 재시도 후에도 실패하면 사용자에게 오류를 반환하고 실패 카운터를 남긴다.
5. 수집 파이프라인이 원본 로그로 적재한다.

## 3.7 데이터셋 생성

1. 스케줄러가 정해진 시각에 데이터셋 생성 요청을 큐에 넣는다.
2. 데이터셋 워커가 대상 기간(기본값은 최근 30일)의 피드백 원본 로그를 읽고, 검색 시점의 맥락이 온전한 이벤트만 골라 학습 가능한 형태로 바꾼다. 청크 본문은 현재 DB에서 다시 읽어 채운다.
3. 결과를 버전 단위 산출물로 저장한다. 학습용과 평가용은 분리해 관리한다.

## 3.8 재학습·평가·서빙 전환

1. 스케줄러가 정해진 요일과 시각에 학습 요청을 큐에 넣는다.
2. 학습 워커가 최신 데이터셋 버전을 고르고 실행 슬롯을 확보한다. 이미 실행 중이면 대기 실행 하나만 남긴다.
3. 후보 모델을 학습해 아티팩트로 저장한다.
4. 학습셋과 분리된 평가셋으로 후보 모델과 기준 모델을 비교한다. 집계 결과는 Metadata DB에, 질의별 상세는 산출물로 저장한다.
5. 통과하면 실행을 배포 준비 상태로 올리고 배포를 시도한다. 못 미치면 실패로 기록하고 끝낸다. 기존 서빙은 그대로다.
6. 배포는 후보 모델 로드와 준비 확인 → 오래된 세대 재색인 조건 확인 → 마지막 정상 조합 스냅샷 저장 → 릴리스 레코드 갱신 → 임베딩 엔드포인트와 Search Service에 교체 통보 순으로 진행한다.
7. 전환 후 직전 조합은 previous로 남아 검색에 함께 쓰인다. 온라인 검색은 최대 두 세대까지만 함께 지원한다.
8. 그보다 오래된 세대에 남은 데이터는 재색인 워커가 최신 기준으로 순차 재색인한다.

## 3.9 롤백과 복구

1. 운영자가 롤백을 요청하면 Core API가 현재 릴리스를 확인하고 롤백 요청을 큐에 발행한다.
2. 롤백 워커가 요청에 담긴 예상 모델 버전과 전환 시각을 현재 릴리스와 대조한다. 다르면 오래된 요청으로 보고 아무것도 하지 않는다.
3. 문제 모델 기간에 만들어진 데이터가 있는 프로젝트를 검색에서 제외하고 릴리스를 롤백 준비 상태로 바꾼다.
4. 복원 대상 모델이 준비되고 인덱스 스냅샷이 복원되면 릴리스를 마지막 정상 상태로 되돌리고, 전환 중 후보 정보는 비운다.
5. 스케줄러가 주기적으로 복구 대상을 훑어 영상별 재임베딩 요청을 넣고, 복구가 끝난 프로젝트부터 다시 검색 범위에 넣는다. 이 재임베딩은 롤백 완료와 분리된 후속 작업이다.

---

# 4. 상태 모델

## 4.1 Video.status

`PENDING` → `UPLOADED` → `PROCESSING` → `READY`

- 어느 단계에서든 오류가 나면 `FAILED`가 되고 실패 단계·실패 코드·추적 ID가 함께 기록된다.
- 어느 상태에서든 삭제 요청이 오면 `DELETING`이 되고, 연쇄 삭제가 끝나면 레코드 자체가 사라진다.
- `READY`인 영상을 다시 처리할 때는 상태를 유지한 채 처리권만 잡는다.

## 4.2 Project.lifecycle_state / search_serving_state

| 컬럼 | 값 | 뜻 |
|---|---|---|
| `lifecycle_state` | `ACTIVE`, `DELETING` | 삭제 중인 프로젝트는 새 영상을 받지 않고 검색에서도 빠진다 |
| `search_serving_state` | `SERVABLE`, `ROLLBACK_EXCLUDED` | 롤백 복구 중인 프로젝트만 일시적으로 제외된다 |

프로젝트가 검색 가능하려면 `ACTIVE`이고 `SERVABLE`이며 소속 영상이 모두 `READY`여야 한다. 셋 중 하나라도 어긋나면 검색 후보에 들어가지 않는다.

## 4.3 ModelRelease.release_status

`STABLE` → `CANDIDATE_REINDEXING` → `STABLE`, 또는 `STABLE` → `ROLLBACK_PREPARING` → `STABLE`.

`ROLLBACK_PREPARING` 동안에는 신규 영상 적재가 막힌다.

## 4.4 MLPipelineRun.status

`PENDING`, `RUNNING`, `READY_FOR_RELEASE`, `DEPLOY_COMPLETED`, `FAILED`, `SUPERSEDED`, `DEPLOYMENT_BLOCKED`.

`RUNNING`과 `PENDING`은 각각 동시에 하나만 존재할 수 있다. 데이터베이스 부분 유니크 인덱스로 강제한다.

## 4.5 그 밖의 상태값

| 대상 | 값 |
|---|---|
| `ModelSnapshot.status` | `ACTIVE`, `PREVIOUS_STABLE`, `ROLLED_BACK`, `SUPERSEDED` |
| `ModelEvaluation.status` | `RUNNING`, `COMPLETED`, `FAILED` |
| `ModelEvaluation.overall_decision` | `PASS`, `FAIL` |
| `LegacyReindexItem.status` | `PENDING`, `RUNNING`, `SUCCEEDED`, `FAILED`, `SKIPPED` |
| `AppUser.role` / `AppUser.status` | `USER`·`ADMIN` / `ACTIVE`·`DISABLED` |

---

# 5. 데이터 모델

## 5.1 AppUser
- `id`, `email`(고유), `password_hash`
- `role`: `USER` / `ADMIN`
- `status`: `ACTIVE` / `DISABLED`
- `created_at`, `updated_at`, `last_login_at`

## 5.2 Project
- `id`, `user_id`, `title`, `description`
- `search_serving_state`: `SERVABLE` / `ROLLBACK_EXCLUDED`
- `lifecycle_state`: `ACTIVE` / `DELETING`
- `created_at`, `updated_at`

## 5.3 Video
- `id`, `project_id`, `user_id`, `title`
- `category`: `GENERAL` / `IT` / `MEDICAL` / `LEGAL`
- `input_type`: `LOCAL_FILE` / `EXTERNAL_URL`
- `source_url`: 외부 URL 입력 시 원본 링크
- `storage_path`: 원본 파일 저장 경로
- `status`: `PENDING` / `UPLOADED` / `PROCESSING` / `READY` / `FAILED` / `DELETING`
- `failed_stage`: `DOWNLOAD` / `EXTRACT` / `STT` / `CHUNKING` / `EMBEDDING` / `VECTOR_UPSERT`
- `failure_code`: 사용자에게 보여줄 실패 사유 분류
- `failure_trace_id`: 실패한 요청의 추적 ID
- `processing_claimed_at`: 처리권 확보 시각. 오래 멈춘 항목을 다시 가져가는 기준
- `created_at`, `updated_at`

## 5.4 Asset
- `id`, `video_id`
- `asset_type`: `AUDIO` / `KEYFRAME`
- `storage_path`
- `start_ms`, `end_ms`: 오디오는 전체 길이, 키프레임은 매핑된 청크 구간

## 5.5 TranscriptSegment
- `id`, `video_id`, `segment_index`
- `text`, `start_ms`, `end_ms`
- `stt_model_version`

## 5.6 Chunk
- `id`, `video_id`, `chunk_index`
- `text`: 음성 인식과 청킹 결과 원본
- `enriched_text`: 검색용 합성 텍스트. 원본 + 화면 설명 + OCR + 장면 태그. 보강 결과가 없으면 원본과 같다
- `visual_caption`, `ocr_text`, `scene_tags`: 대표 키프레임에서 뽑은 원문. 없으면 빈 문자열
- `start_ms`, `end_ms`, `keyframe_asset_id`
- `chunking_version`, `stt_model_version`, `embedding_model_version`

## 5.7 VectorIndexEntry (파생)
- `index_name` + `chunk_id` 복합 기본키. 모델 버전별로 인덱스를 나눈다
- `user_id`, `project_id`, `video_id`: 검색 범위 필터용
- `embedding_vector`, `embedding_model_version`, `created_at`

## 5.8 SearchResponseSnapshot
검색 응답 시 만들어지는 불변 기록. 피드백 검증과 운영 추적에 쓰고 만료 시각 기준으로 지운다.
- `req_id`(기본키), `user_id`, `project_id`, `query_text`
- `topk_chunk_ids`: 최종 게이트를 통과해 실제 응답에 쓰인 청크 목록(순위 순)
- `used_chunk_ids`: 답변이 실제로 인용한 청크 목록
- `active_model_version`, `active_index_name`
- `served_vector_paths`: 실제 조회한 검색 경로 목록. 항목마다 역할·모델 버전·인덱스 이름
- `project_serving_state`, `scope_notice`
- `created_at`, `expires_at`

## 5.9 SearchConversation
검색 기록 화면에 쓰는 대화 기록. 스냅샷과 달리 만료가 없다.
- `req_id`(기본키), `user_id`, `project_id`
- `query`, `answer`
- `sources`: 응답 근거 목록. 인용 번호·청크·영상·제목·구간·실제 인용 여부
- `created_at`

## 5.10 FeedbackEvent (Object Storage 논리 모델)
- `schema_version`, `event_id`, `req_id`, `user_id`, `project_id`, `trace_id`
- `query_text`, `rating`(`LIKE` / `DISLIKE`)
- `topk_ids`, `used_ids`
- `active_model_version`, `active_index_name`, `served_vector_paths`
- `response_snapshot_ref`, `created_at`

`event_id`는 사용자·요청·평가값으로 결정되므로 같은 입력은 항상 같은 값이 된다. 중복 제거의 기준이다.

## 5.11 ModelEvaluation
- `id`, `candidate_model_version`, `baseline_model_version`
- `evaluation_dataset_ref`, `sample_count`
- `status`, `quality_metrics`, `pass_criteria`, `overall_decision`, `fail_reason`
- `created_at`

질의별 상세 비교 결과는 테이블이 아니라 별도 파일 산출물로 남긴다.

## 5.12 MLPipelineRun
- `id`, `status`, `failed_stage`, `failure_type`(`FAIL` / `ERROR`), `failure_reason`
- `candidate_model_version`, `candidate_index_name`, `baseline_model_version`
- `dataset_version`, `evaluation_id`, `cutover_time`
- `deployment_attempt_count`, `last_deployment_attempt_at`, `deployment_blocked_at`
- `superseded_by_run_id`, `created_at`, `updated_at`

## 5.13 ModelRelease
서빙 상태의 SOT. 한 행만 존재하도록 강제한다.
- `release_status`
- `active_model_version`, `active_index_name`
- `previous_model_version`, `previous_index_name`
- `candidate_model_version`, `candidate_index_name`, `candidate_opened_at`, `candidate_ready_at`
- `switched_at`, `created_at`, `updated_at`

롤백 복원 지점은 이 행이 아니라 `ModelSnapshot` 등록부가 관리한다.

## 5.14 ModelSnapshot
롤백 복원 지점 등록부.
- `snapshot_id`, `model_version`, `index_name`
- `status`: `ACTIVE` / `PREVIOUS_STABLE` / `ROLLED_BACK` / `SUPERSEDED`
- `previous_snapshot_id`, `captured_at`, `created_at`

## 5.15 VectorIndexCatalog
인덱스 수명 관리 대장.
- `index_name`(기본키), `model_version`, `embedding_dimension`
- `retired_at`, `delete_after`, `deleted_at`, `retire_reason`
- `created_at`

## 5.16 LegacyReindexItem
오래된 세대 인덱스에 남은 영상을 최신 인덱스로 옮기는 작업 단위.
- `id`, `video_id`, `user_id`, `project_id`
- `source_index_name`, `source_model_version`, `target_index_name`, `target_model_version`
- `status`, `failed_stage`, `failure_type`, `last_error`, `retry_count`
- `total_chunk_count`, `completed_chunk_count`
- `started_at`, `completed_at`, `created_at`, `updated_at`
- 같은 영상·출발 인덱스·도착 인덱스 조합은 하나만 존재한다

---

# 6. 메시지 계약

영상 처리 계열 메시지와 제어 계열 메시지는 규격이 다르다.

## 6.1 영상 처리 메시지

| 필드 | 설명 |
|---|---|
| `message_type` | `PREPROCESS_REQUEST` / `DELETE_REQUEST` / `PROJECT_DELETE_REQUEST` |
| `payload_version` | 스키마 버전 |
| `trace_id` | 요청 추적 ID. 내부 호출과 로그가 같은 값을 물려받는다 |
| `attempt` | 재발행 횟수. 최초 1 |
| `video_ids` | 대상 영상 목록 |
| `project_id` | 프로젝트 삭제 대상 |
| `issued_at` | 발행 시각 |

- `PREPROCESS_REQUEST`와 `DELETE_REQUEST`는 `video_ids`를 요구하고 `project_id`를 받지 않는다.
- `PROJECT_DELETE_REQUEST`는 `project_id`만 요구하고 `video_ids`를 받지 않는다.
- 페이로드에는 식별자만 담는다. 상세 정보는 워커가 DB에서 다시 읽는다.

## 6.2 제어 메시지

| 메시지 | 내용 |
|---|---|
| `TRAINING_REQUEST` | 공통 필드만 사용한다. 영상 식별자를 담지 않는다 |
| `ROLLBACK_REQUEST` | 공통 필드에 더해 `expected_active_model_version`과 `expected_switched_at`을 반드시 담는다. 이 값이 현재 릴리스와 다르면 워커가 오래된 요청으로 보고 무시한다 |
| `DATASET_GENERATION_REQUEST` | 데이터셋을 만들 기간의 시작과 끝을 담을 수 있다. 스케줄러는 이 값을 넣지 않으며, 비어 있으면 데이터셋 워커가 발행 시각 기준 최근 30일을 쓴다 |
| `REEMBEDDING_REQUEST` | 대상 영상과 목표 모델 버전·인덱스 이름을 담는다 |

## 6.3 큐 구성

| 큐 | 소비자 |
|---|---|
| `PREPROCESS_REQUEST`, `DELETE_REQUEST`, `PROJECT_DELETE_REQUEST` | Pipeline Worker |
| 데이터셋 큐 | Feedback Loop Pipeline `dataset-worker` |
| 학습 큐 | Feedback Loop Pipeline `train-release-worker` |
| 롤백 큐 | Feedback Loop Pipeline `rollback-worker` |
| 재임베딩 큐 | Feedback Loop Pipeline `reembedding-worker` |

큐마다 가시성 타임아웃이 다르다. 처리가 오래 걸리는 전처리 큐는 길고, 삭제 큐는 짧다. 워커가 처리권을 다시 가져가는 기준 시간은 전처리 큐의 가시성 타임아웃보다 반드시 짧아야 하며, 시작 시점에 검증한다.

---

# 7. 배포 구조와 구현 매핑

## 7.1 논리 이름과 실제 기술

| 논리 이름 | 현재 구현 |
|---|---|
| Metadata DB (SOT) | Compute Engine VM 위의 PostgreSQL 16 |
| Vector Store | 같은 PostgreSQL 안의 `vector_index_entry` 테이블, pgvector 확장 |
| Message Broker | 같은 PostgreSQL 안의 pgmq |
| 키워드 검색 | PostgreSQL `to_tsvector` + `plainto_tsquery` |
| Object Storage | Cloud Storage 버킷 3개 (영상, 피드백 로그, ML 산출물) |
| Managed Embedding Endpoint | BGE-M3 CPU 추론. Compute Engine VM 2대 (배치용, 검색용) |
| 임베딩 모델 학습 | Feedback Loop Pipeline 안의 로컬 학습 러너 |
| LLM | Vertex AI Gemini |
| 음성 인식 | Google Speech-to-Text |
| 화면 정보 추출 | Vertex AI Gemini Vision |
| 피드백 수집 파이프라인 | Vector 0.54 설정 파일 |
| 비밀값 | Secret Manager |

Metadata DB, Vector Store, Message Broker는 논리적으로만 분리되어 있고 실제로는 같은 PostgreSQL 인스턴스다.

## 7.2 배포 단위

| 배포 단위 | 형태 | 비고 |
|---|---|---|
| frontend | Cloud Run 서비스 | 유일한 공개 진입점 |
| core-api | Cloud Run 서비스 | 프론트엔드만 호출 가능 |
| search-service | Cloud Run 서비스 | 프론트엔드와 부하 테스트 러너만 호출 가능 |
| feedback-ingestion-pipeline | Cloud Run 서비스 | Core API만 호출 가능 |
| pipeline-worker | HTTP 없는 폴링 워커 | 큐를 계속 폴링한다 |
| feedback-loop-* (6개) | HTTP 없는 폴링 워커 | 역할별로 별도 배포 단위 |
| 임베딩 VM (배치용) | Compute Engine VM | Pipeline Worker와 재색인·재임베딩 워커가 사용 |
| 임베딩 VM (검색용) | Compute Engine VM | Search Service 전용. 배치 부하와 분리 |
| PostgreSQL VM | Compute Engine VM | 메타데이터·벡터·큐를 모두 담당 |
| 데이터베이스 마이그레이션 | Cloud Run 작업 | 배포 시 1회 실행 |
| 모델 릴리스 초기화 | Cloud Run 작업 | 릴리스 레코드가 없을 때 부트스트랩 |
| 부하 테스트 러너 VM | Compute Engine VM | k6 실행용 |

임베딩 엔드포인트는 같은 컨테이너 이미지를 두 VM에 배포한다. 검색 지연이 배치 임베딩 부하에 밀리지 않도록 사용처를 나눈 것이다.

## 7.3 호출 관계

- 브라우저 → frontend (공개 HTTPS)
- frontend → core-api / search-service (플랫폼 IAM 토큰 첨부)
- core-api → PostgreSQL, Object Storage, feedback-ingestion-pipeline
- search-service → 검색용 임베딩 VM, PostgreSQL, LLM
- pipeline-worker → PostgreSQL(큐 포함), Object Storage, 음성 인식, Vision, 배치용 임베딩 VM
- feedback-loop-* → PostgreSQL(큐 포함), Object Storage, 두 임베딩 VM, search-service 내부 경로
- 임베딩 VM → PostgreSQL(릴리스 레코드), Object Storage(모델 아티팩트)

임베딩 VM과 PostgreSQL VM은 VPC 내부 주소로만 접근한다.

## 7.4 모델 교체가 전파되는 경로

1. Feedback Loop Pipeline이 릴리스 레코드를 갱신한다.
2. 두 임베딩 VM에 모델 재로드를 요청한다.
3. Search Service 내부 경로로 검색 대상 재조회를 요청한다.
4. Pipeline Worker는 별도 통보 없이 다음 영상을 처리할 때 릴리스 레코드를 다시 읽는다.
