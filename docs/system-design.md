# 1. Architecture Overview

## 1.1 주요 컴포넌트

### 클라이언트 서브시스템 (Client Subsystem)

**목적**: 사용자의 디바이스(브라우저)에서 실행되며, 백엔드 시스템과 상호작용할 수 있는 시각적 인터페이스를 제공한다.

#### Client (User Web UI)

1. 업로드 및 검색 UI 제공: 로컬 파일 업로드 또는 외부 URL 입력, 자연어 질의 입력, 검색 결과(근거 구간/타임스탬프) 표시. 타임스탬프 클릭 시 영상 재생은 영상 출처(input_type)에 따라 분기 처리한다. LOCAL_FILE은 Core API Server로부터 재생용 보안 URL을 동적으로 발급받아 내장 임베디드 플레이어로 재생하고, EXTERNAL_URL은 백엔드를 거치지 않고 원본 외부 링크(source_url)와 타임스탬프 정보를 활용하여 클라이언트 내부 계층에서 직접 외부 플랫폼과 연동하여 재생 위치를 제어한다.
2. 처리상태 조회 및 목록 관리: 업로드한 영상 목록 조회, 제목 수정/삭제, 처리 상태(업로드 완료/처리 중/완료) 조회
3. 사용자 피드백 입력: 좋아요/싫어요 등 명시적 피드백을 입력하고, 피드백이 수집될 수 있도록 이벤트를 발생
4. 카테고리 선택: 영상 업로드시 영상 도메인 카테고리(일반, IT, 의학, 법률 등) 선택

---

### 리버스 프록시 서브시스템 (Reverse Proxy Subsystem)

**목적**: 시스템 인프라(VPC)의 단순 진입점으로서, 외부 트래픽을 수신하여 부하 분산을 수행하고 내부 백엔드 서비스로 경로를 라우팅한다.

#### Reverse Proxy / Load Balancer

1. 요청 수신 및 라우팅: 외부 요청을 수신하고, 경로(Path)에 따라 파일 업로드 및 상태 조회는 Core API Server로, 검색 질의는 Search Service로 단순 라우팅한다. Authorization 헤더는 조작 없이 백엔드로 그대로 전달한다. 
2. 트래픽 분산 및 가용성: 트래픽 증가 시 요청을 백엔드 인스턴스들로 분산시켜 서비스 중단을 방지한다.
3. 기본 네트워크 보안: Rate limiting 및 기본 IP 차단 정책 등을 적용하여 비정상적인 트래픽(DDoS 등)으로부터 내부 서비스를 1차적으로 보호한다.

---

### 애플리케이션 서브시스템 (Application Subsystem)

**목적:** 게이트웨이를 통과한 클라이언트의 요청을 받아, 시스템의 핵심 도메인 로직(url 발급, 텍스트/벡터 검색, 메타데이터 저장, 피드백 수집 등)을 전담하여 처리하는 독립적인 백엔드 애플리케이션 그룹이다.

#### Core API Server

1. 사용자 요청 처리: 영상 업로드, 처리 상태 조회, 영상 목록 관리 등 클라이언트 요청을 처리한다.
2. presigned URL 발급: 권한 확인 후 업로드 가능한 URL과 영상 고유 id 를 발급
3. 메타데이터 및 초기 상태 저장: 영상에 대한 메타데이터(업로더, 영상이름,카테고리)와 초기 상태를 db에 저장
4. 업로드 완료 확인/처리 트리거: 클라이언트로 부터 업로드가 끝났다는 신호를 받아 파이프라인 작업을 큐에 넣고, 상태를 업데이트
5. 인증 및 인가(AuthN/AuthZ): 전달받은 Access Token(JWT)의 서명과 만료를 내부 미들웨어에서 직접 검증한다. 이후 claim에서 추출한 requester_user_id를 기준으로 영상 리소스(video_id)의 소유권을 확인하여, 본인이 업로드한 영상과 데이터에만 접근하도록 제어한다.
6. 피드백 수집(Feedback Ingestion): 검색 결과에 대한 좋아요/싫어요를 영상 단위가 아닌 검색 응답 단위(`req_id`)로 수집하며, 클라이언트는 Search Service 응답의 `chunks`에서 `topk_ids`, `used_ids`를 파생해 해당 시점의 `query_text`와 함께 저장한다.
7. 비동기 작업 요청: 영상 처리처럼 시간이 오래 걸리는 작업은 직접 처리하지 않고 메시지 브로커로 전달하여, 즉시 “요청 접수(Accepted)” 응답을 반환한다.
8. 영상 삭제 요청 처리: 사용자의 삭제 요청을 수신하여 Video.status를 DELETING으로 전이하고 DELETE_REQUEST를 메시지 브로커에 발행한다. 실제 연쇄 삭제는 Pipeline Worker에 위임한다.
9. 파이프라인 실패 재처리: 사용자의 재시도 요청을 수신하여 Video.status를 PENDING으로 초기화하고 PREPROCESS_REQUEST를 재발행하여 Worker가 실패 지점부터 재개할 수 있도록 한다.


#### Search Service

1. 검색 전담 처리: 자연어 질의를 수신하여 하이브리드 검색 파이프라인(임베딩 변환 및 DB/Vector 조회)을 즉시 시작한다.
2. 하이브리드 검색 오케스트레이션 (Search Orchestrator):
   - 내부 추상화 인터페이스를 통해 **Managed Embedding Endpoint를 직접 호출**하여 질의 텍스트를 벡터로 변환한다.
   - 키워드 후보는 Metadata DB(FTS)에서, 벡터 후보는 Vector Store(ANN)에서 각각 생성한다.
   - 두 후보를 RRF(Reciprocal Rank Fusion) 등으로 병합하여 최종 Top-K를 만든다.
   - 최종 응답 전에 Metadata DB(SOT)에서 최종 서빙 검증을 수행하여 (권한/존재 여부(삭제=hard delete)/READY 상태) 기준을 통과한 Chunk만 반환한다.
3. LLM 컨텍스트 주입: 추출된 청크들을 조립한 프롬프트를 `LLM_PROVIDER`가 선택한 Search Service 내부 LLM 인터페이스 구현체(기본값: `GeminiLLMAdapter`)에 전달하여 최종 답변을 생성하며, 피드백 귀속용 `req_id`, 타임스탬프 레퍼런스 `ref`, 실제 인용 여부 `used`가 포함된 `chunks` 배열을 함께 반환한다.
4. 인증 및 테넌시 강제(AuthN/AuthZ): 전달받은 Access Token(JWT)을 직접 검증하여 requester_user_id를 추출한다. 이를 기준으로 검색 대상 범위를 제한하고, Metadata DB(FTS) 및 Vector Store(ANN) 조회 단계 모두에서 사용자 테넌트 필터를 반드시 강제한다.


---

### 비동기 파이프라인 서브시스템 (Async Pipeline Subsystem)

**목적:** API 서버와 무거운 데이터 처리/학습 작업을 비동기적으로 분리(Decoupling)하여 시스템의 응답성을 유지하고, 메시지 큐 기반의 유연한 확장을 통해 대용량 미디어 및 AI 연산을 안정적으로 처리한다.

#### Message Broker

1. 작업 대기열 관리: API 서버로부터 전달받은 '영상 처리 요청'을 큐(Queue)에 적재하여, Worker가 처리 가능한 시점에 작업을 가져가도록 한다.
2. 워크로드 격리: 모델 학습(Training) 큐를 별도로 운영하여, 학습 부하가 업로드/검색 처리에 영향을 주지 않도록 분리한다.
3. 메시지 계약(Message Contract): 파이프라인 메시지는 공통 Envelope + 메시지별 Payload로 구성하며, 최소 필드/스키마는 Data Model의 “Message Contract(3.7~)” 정의를 따른다.

#### Media & AI Pipeline Worker (통합 워커)

1. 단일 파이프라인 실행: 큐에서 PROCESS_REQUEST 수신 시, 하나의 프로세스 내에서 `다운로드 → 추출 → STT → 청킹 → 임베딩 → DB 적재`를 순차적으로 논스톱 처리하여 네트워크 I/O 지연을 극소화한다.
2. 멱등성 보장 다운로드 및 전처리: 외부 URL 인입 시 대상 파일이 스토리지에 존재하는지 확인(멱등성 체크) 후, 없으면 다운로드하여 상태를 UPLOADED로 갱신한다. 이후 로컬 환경에서 오디오와 키프레임을 추출한다.
3. 데이터 지역성 기반 AI 서비스 직접 연동: 추출된 오디오 데이터를 스토리지 다운로드 대기 없이 로컬 환경에서 추상화된 AI 클라이언트 인터페이스를 통해 **External AI Adapters(STT API)**로 직접 전송하여 텍스트 스크립트를 반환받는다. (추출된 원본 오디오 및 키프레임은 장애 복구 및 서비스 서빙을 위해 Object Storage에 비동기 백업 적재한다.)
4. 시맨틱 청킹 및 벡터화: 변환된 스크립트를 문맥 단위로 분할(Chunking)하고 타임스탬프에 맞는 키프레임을 매핑한다. 청킹 및 Vision enrichment 단계는 추출 직후 확보한 로컬 키프레임 메타데이터를 즉시 사용하며, Object Storage 백업 및 Asset 레코드 기록은 병렬로 진행하되 최종 Chunk 적재 전에는 완료되어 `keyframe_asset_id`를 확정해야 한다. 이후 텍스트 청크를 **Managed Embedding Endpoint**로 직접 전송하여 임베딩 벡터를 반환받는다.
5. 검색 색인 구축 및 완료: 전체 대본/청크 메타데이터는 Metadata DB(SOT)에 트랜잭션 적재하고, 임베딩 벡터는 Vector Store(ANN)에 Upsert 한다. 반영 완료 시 Video.status를 READY로 갱신한다.
6. 부분 실패 및 장애 복구: 작업 실패 시 기존 산출물(스토리지 백업본)을 보존하며 DB에 failed_stage를 기록한다. 재처리 요청 시 완료된 무거운 작업(영상 다운로드 등)은 건너뛰고(Skip), failed_stage와 보존 산출물을 함께 참조하여 안전한 재개 지점을 판단할 수 있도록(Resume) 복구력을 보장한다.
7. 영상 삭제 연쇄 처리: DELETE_REQUEST 수신 시, 또는 파이프라인 처리 중 각 단계 진입 전 Video.status=DELETING을 감지한 경우 현재 단계에서 중단하고 연쇄 삭제를 수행한다. Metadata DB의 관련 레코드(VectorIndexEntry, Chunk, TranscriptSegment, Asset, Video)를 트랜잭션으로 삭제하고, Object Storage 파일(원본 영상, 오디오, 키프레임)은 메인 서비스와 분리하여 비동기로 정리한다.

#### Model Training Worker

1. 데이터셋 전처리: 사용자 피드백 및 관리자가 확정한 학습용 데이터셋을 로드하여 학습 가능한 포맷으로 변환한다.
2. 모델 성능 개선: 피드백 기반 데이터셋을 활용해 임베딩 모델을 개선하고, 필요 시 기존 벡터 데이터를 재색인한다.
3. 모델 파인 튜닝: 구축된 특정 도메인의 데이터셋을 활용해 범용 임베딩 모델을 특정 도메인(법률, 의료 등) 데이터에 맞춰 재학습(Fine-tuning)한다.
4. 자동 평가(Auto-Evaluation): 후보 모델과 현재 운영 모델의 성능을 비교 평가하고, 개선이 검증된 경우에만 배포 가능한 상태로 등록한다.
5. 메시지 소비: TRAINING_REQUEST를 소비하여 학습/평가/배포 파이프라인을 수행한다. (상세 스키마는 Data Model 참조)
---

### AI 추론 서브시스템 (AI Inference Subsystem)

**목적:** 무거운 AI 연산 자원을 워커와 물리적으로 분리하여(Dedicated) 자원 효율성과 독립적인 오토스케일링을 확보한다.

#### Managed Embedding Endpoint (자체 호스팅 모델)

1. 임베딩 추론 전담: Worker 및 Search Service로부터 API 요청을 직접 수신하여 텍스트를 벡터로 변환하는 연산에만 집중한다.
2. 모델 관리 및 서빙 기준: 배포 시점에 지정된 모델 파일을 프로세스 시작 시 로드하여 서빙한다. 모델 교체는 최신 버전 자동 감지가 아니라 파일 교체 후 새 프로세스 기동 방식으로 반영하며, 초기 단계에서는 운영자 개입을 전제로 한다.
3. 서빙 준비성(readiness): 모델 파일 로드와 고정 내부 smoke inference가 모두 성공해야만 요청을 받는다. 현재 서빙 중인 모델 버전의 기준값은 실제 로드한 artifact path이다.

#### External AI Adapters (외부 API)

1. STT 연동: Google Cloud Speech-to-Text 등 외부 상용 음성 API와의 통신 및 에러 핸들링(Retry, 서킷 브레이커 등)을 담당하는 **추상화된 연결 모듈(Adapter)**이다. Pipeline Worker가 구체적인 외부 API 라이브러리나 통신 규격에 직접 결합되지 않도록 인터페이스를 분리하여 설계한다.


---

### 데이터 저장소 (Data & Storage Subsystem)

**목적:** 데이터의 특성에 따라 저장소를 분리하여 성능/내구성/검색 효율을 확보

#### Object Storage

1. 대용량 파일 저장: 원본 영상, 추출된 오디오, 키프레임 이미지 등 대용량 파일을 저장한다.

#### Metadata DB (Source of Truth; RDB)

1. 정합성 보장(SOT): 사용자, 영상 메타데이터, 상태(Status), Transcript/Chunk(텍스트/타임스탬프/참조), 피드백을 ACID 트랜잭션으로 저장한다.
2. 키워드 검색(FTS): Chunk의 enriched_text(없을 경우 text)에 대한 FTS 인덱스를 운영하여 키워드 후보를 생성한다. (초기 구성: RDB 내 FTS)
3. 최종 서빙 검증(SOT Validation): 검색 결과로 반환되기 전, 권한/존재 여부(삭제=hard delete)/상태(READY) 기준으로 노출 가능한 Chunk만 최종 확정하는 기준 저장소로 동작한다.

#### Vector Store (ANN Index; Derived Projection)

1. 벡터 저장(파생 인덱스): Chunk 단위 임베딩 벡터를 저장하며, 검색 시 테넌시 필터를 적용할 수 있도록 최소 메타데이터(user_id, video_id 등)를 함께 보유한다.
2. 최종 일관성: Vector Store는 Metadata DB로부터 파생된 Projection이며, 부분 실패/지연이 발생할 수 있다. 사용자 노출 정합성은 Metadata DB의 상태(READY) 및 최종 서빙 검증(SOT Validation)으로 보장한다.


#### Model Artifact Files

1. 모델 버전 관리: 임베딩 모델 파일과 버전 메타데이터는 파일 단위의 배포 아티팩트로 관리한다.
2. 버전 식별 기준: 서빙 중 모델 버전의 SOT는 실제 로드한 artifact path이며, 경로명은 고정 naming convention에 따라 version string을 포함해야 한다.
3. 서빙 반영 방식: Managed Embedding Endpoint는 프로세스 시작 시 지정된 로컬 경로의 모델 파일을 로드한다. 모델 승격은 운영자가 선택한 아티팩트를 배포하고 새 프로세스를 기동하는 절차로 반영한다.

---

### 관리 및 운영 (Admin & Ops)

**목적:** 운영자가 파이프라인/모델 품질을 관리하고 장애 대응 및 배포 자동화를 수행

#### Admin Dashboard

* 모니터링: 파이프라인 단계별 성공/실패 여부, 소요 시간, 대기열 적체 등 운영 지표를 시각화하여 실시간에 가깝게 상태를 제공한다.
* 데이터셋 구축 도구: 수집된 사용자 피드백 데이터를 검수하고, 학습용 데이터셋으로 확정(Commit)하는 UI를 제공한다.
* 학습 파이프라인 제어: 학습 진행 상황 및 평가 리포트를 시각화하여 제공한다.

#### Observability (Logging / Metrics)

* 운영 데이터 수집: 파이프라인 로그, 처리 시간, 실패 원인, 큐 적체량 등 관측 데이터를 수집/저장하여 Admin Dashboard에서 조회 가능하도록 한다.
* 장애 분석 지원: 특정 단계 실패 시 원인 파악과 재처리 판단에 필요한 근거 데이터를 제공한다.

#### Pipeline Controller

* 오케스트레이션 및 정합성 관리: 파이프라인 상태를 기준으로 단계 실행 순서, 재시도를 관리한다. 영상 삭제 시 연쇄 삭제(Cascade Delete) 워크플로우는 Pipeline Worker가 담당한다.
* 모델 교체 및 재색인 트리거: 임베딩/STT 모델 버전 변경 시, 기존 데이터를 새로운 모델로 다시 벡터화(재색인)하는 작업을 트리거한다.
* 배포/롤백 제어: 평가를 통과한 모델을 배포하고, 문제 발생 시 이전 버전으로 즉시 롤백할 수 있도록 제어 로직을 제공한다.

---

## 1.2 시스템 라이프사이클 (상태 전이 흐름)

단일 워커 통합 및 AI 모델 직접 호출 구조를 통해 컴포넌트 간 네트워크 이동 경로가 대폭 간소화되었다. 전체 시스템의 데이터 무결성과 파이프라인 진행 제어는 Metadata DB의 **상태(Status)**를 기준으로 통제된다. 구체적인 I/O 명세는 `2. Data Flow`를 따른다.

**Status 전이 흐름:**
1. **정상 전이:** `PENDING` (요청 인입) → `UPLOADED` (영상 원본 확보) → `PROCESSING` (추출 및 AI 분석 중) → `READY` (검색 가능 상태)
2. **예외 전이:** 진행 중 어느 단계에서든 오류 발생 시 `FAILED`로 전이되며, DB에 `failed_stage`를 기록하여 재시도 시 실패 분류 및 재개 판단 근거로 활용한다.
3. **삭제 전이:** 임의 상태에서 사용자가 삭제 요청 시 `DELETING`으로 전이된다. 이 시점부터 해당 영상은 검색 범위에서 즉시 제외된다. Pipeline Worker가 연쇄 삭제를 완료한 후 레코드를 hard-delete한다.


---

# 2 Data Flow

## 2.1 Video Ingest (Local File & External URL)

**입력**
- 주체: Client
- 데이터: 로컬 영상 파일 (binary) 또는 외부 영상 URL, 메타데이터 (제목, 카테고리)

**처리**
1. Core API Server가 고유 식별자(UUID 등)를 직접 생성하여 video_id를 할당하고, Object Storage에 저장될 객체 키(storage_path)를 미리 결정한다.
2. Core API Server가 메타데이터(제목, 카테고리, 원본 URL 등)와 확정된 storage_path를 한 번의 트랜잭션으로 Metadata DB에 저장한다. (status=PENDING)
3. **[Local File 인입 시]** storage_path를 포함한 Presigned URL을 발급하여 반환한다. Client가 영상 업로드 후 완료 신호를 보내면 status를 UPLOADED로 갱신하고 Message Broker에 전처리 작업 큐(PREPROCESS_REQUEST)를 발행한다.
4. **[External URL 인입 시]** 다운로드를 Worker에 위임하기 위해 즉시 Message Broker에 전처리 작업 큐(PREPROCESS_REQUEST)를 발행한다.

**출력**
- 반환: (Local File 한정) Presigned URL + 영상 고유 ID → Client 반환
- 이벤트: 전처리 작업 메시지(PREPROCESS_REQUEST) → Message Broker 발행

**저장 위치**
| 데이터 | 저장소 |
|--------|--------|
| 영상 원본 파일 | Object Storage |
| 영상 메타데이터 (제목, 카테고리, 업로더, source_url, status=PENDING/UPLOADED) | Metadata DB |

## 2.2 Media Processing & AI Indexing (통합 파이프라인)

**입력**
- 주체: Media & AI Pipeline Worker (Message Broker로부터 PREPROCESS_REQUEST 소비)
- 데이터: 영상 원본 파일 (Object Storage) 또는 외부 영상 URL

**처리 (단일 프로세스 내 순차 실행)**
1. Worker가 PREPROCESS_REQUEST를 수신하고 Metadata DB에서 Video 정보를 로드하여 상태 기반 멱등성 및 failed_stage를 체크한다. (완료된 무거운 작업 방지)
2. **[영상 확보]** External URL 인입이면서 스토리지에 파일이 없다면 영상을 다운로드하여 저장하고, Metadata DB의 상태를 UPLOADED로 갱신한다.
3. **[전처리]** 상태를 PROCESSING으로 변경 후 로컬 환경에서 영상을 로드하여 오디오와 키프레임을 추출한다. (네트워크 대기 없이 즉시 4번으로 넘어가며, 추출된 파일은 비동기로 Object Storage에 저장하고 DB에 경로를 남긴다.)
4. **[STT 변환]** 로컬의 오디오 데이터를 **External AI Adapters(외부 STT API)**로 직접 전송하여 텍스트 및 타임스탬프 스크립트를 반환받는다.
5. **[청킹 및 임베딩]** Worker가 전체 스크립트를 문맥 단위로 청킹하고 키프레임을 매핑한다. 텍스트 청크를 **Managed Embedding Endpoint(자체 배포 모델)**로 직접 전송하여 임베딩 벡터를 반환받는다.
6. **[적재 및 완료]** 스크립트/청크(텍스트, 타임스탬프, 참조)는 Metadata DB(SOT)에 적재하고, 임베딩 벡터는 Vector Store(ANN)에 적재(Upsert)한다. 두 저장소 반영 완료 시 status=READY로 갱신한다.

**출력**
- 이벤트: (내부 상태 전이로 인해 별도 완료 큐 발행 없음)

**저장 위치**
| 데이터 | 저장소 |
|--------|--------|
| 오디오 파일, 키프레임 이미지 | Object Storage |
| 오디오/키프레임 경로, 전체 스크립트, 청크 텍스트/참조, status=READY | Metadata DB |
| 청크 임베딩 벡터 (+ 필터용 최소 메타데이터) | Vector Store |


## 2.3 Search & RAG Serving

**입력**
- 주체: Client
- 데이터: 자연어 질의 텍스트, Authorization 토큰, scope(검색 범위; 예: {all_my_videos:true} / {video_ids:[...]})

**처리**
1. Reverse Proxy가 요청을 수신하여 Authorization 헤더를 포함한 원본 요청을 그대로 Search Service로 전달
2. Search Service가 내부 미들웨어를 통해 JWT를 직접 검증하고, claim에서 requester_user_id를 추출하여 테넌시 필터에 사용
3. 질의 텍스트를 **Managed Embedding Endpoint**로 직접 보내 임베딩 벡터로 변환한다.
4. Search Service가 Metadata DB(FTS)에서 키워드 후보 Top-K를 조회 (테넌시/스코프 적용)
5. Search Service가 Vector Store(ANN) 에서 벡터 후보 Top-K를 조회 (테넌시/스코프 적용)
6. Search Service가 키워드/벡터 후보를 병합(RRF)하여 최종 Top-K 후보를 결정
7. Search Service가 Metadata DB(SOT) 를 “서빙 게이트”로 조회하여 (권한/존재 여부(삭제=hard delete)/READY 상태) 검증을 수행하고, 최종 컨텍스트(청크 텍스트/타임스탬프)를 로드한다.
8. Search Service가 Top-K 컨텍스트 + 질의를 서비스 내부 LLM 인터페이스 구현체에 전달하여 최종 답변과 structured `used_refs`를 생성한다.
9. Search Service가 `req_id` + 생성된 답변 + `chunks[{ref, chunk_id, video_id, title, start_ms, end_ms, text, used}]`를 Client에 최종 반환

**출력**
- `req_id` + 생성된 답변 + `chunks` → Client 반환
  - `chunks`: SOT 게이트를 통과해 실제 응답 생성에 사용된 최종 청크의 canonical 배열
  - `chunks[].ref`: 답변 본문 `[n]` 인라인 인용과 대응하는 요청 단위 citation 번호
  - `chunks[].used`: 해당 청크가 실제 답변 근거로 사용되었는지 여부

**저장 위치**
- 검색 응답은 실시간으로 생성 및 반환되며 별도로 영구 저장하지 않음. (단, 피드백 발생 시 2.6 절차에 따라 수집됨)


## 2.4 처리 실패 (FAILED)

**발생 시점**
- Video Ingest, Media Processing & AI Indexing 파이프라인 수행 중 오류 발생 시

**처리**
1. 실패한 컴포넌트(API Server 또는 Worker)가 Metadata DB에 status=FAILED 및 failed_stage를 기록한다.
   - failed_stage 후보: DOWNLOAD / EXTRACT / STT / CHUNKING / EMBEDDING / VECTOR_UPSERT
2. 해당 시점까지 생성된 중간 산출물(오디오, 키프레임, 청크 등)은 삭제하지 않고 보존
3. 사용자가 재시도를 요청하면 Core API Server가 Video.status를 PENDING으로 초기화하고 PREPROCESS_REQUEST를 재발행한다. Worker는 DB의 failed_stage와 보존 산출물을 함께 확인하는 멱등성(Idempotency) 로직을 통해, 이미 완료된 무거운 작업은 건너뛰고(Skip) 안전한 재개 지점부터 처리를 재개(Resume)한다. `failed_stage`는 실패 분류값이며 모든 값이 1:1 재개 지점을 의미하지는 않는다. 예를 들어 `STT`는 STT 호출 또는 TranscriptSegment 적재 실패를 의미하며, 오디오 산출물이 보존된 경우 STT부터 다시 수행한다. `CHUNKING`은 TranscriptSegment가 저장된 경우 청킹부터 다시 수행할 수 있다. `EMBEDDING`은 임베딩 배치 호출 실패를 의미하며, 현재 파이프라인은 임베딩 전 Chunk와 enriched_text를 별도 영속화하지 않으므로 기본값은 `CHUNKING`부터 재수행한다. `VECTOR_UPSERT`는 최종 적재 실패를 의미하며 기본값은 `CHUNKING`부터 재수행한다.
4. 최대 재시도 횟수를 초과했거나 Non-Retryable 오류로 최종 실패한 메시지는 별도 앱 레벨 DLQ로 이동하지 않고, status=FAILED 및 failed_stage, error_message를 기록한 뒤 Ack 처리한다.

**저장 위치**
| 데이터 | 저장소 |
|--------|--------|
| status=FAILED, failed_stage, error_message | Metadata DB |

## 2.5 영상 삭제 (Video Deletion)

**발생 시점**
- 사용자가 본인이 업로드한 영상 삭제를 요청할 때

**처리**
1. Core API Server가 JWT를 검증하여 requester_user_id를 추출하고 영상 소유권을 확인한다.
2. Core API Server가 Metadata DB의 Video.status를 DELETING으로 전이한다. 이 시점부터 해당 영상은 SOT 게이트의 READY 상태 필터에 의해 검색 범위에서 즉시 제외된다.
3. Core API Server가 Message Broker에 DELETE_REQUEST를 발행하고 202 Accepted를 반환한다.
4. **[파이프라인 진행 중인 경우]** Pipeline Worker가 각 단계 진입 전 Video.status를 확인하여 DELETING을 감지하면 현재 지점에서 파이프라인을 중단하고 정리 모드로 전환한다. (외부 API 추가 호출 없이 즉시 중단)
5. Pipeline Worker가 연쇄 삭제를 순서대로 수행한다.
   - Metadata DB: VectorIndexEntry, Chunk, TranscriptSegment, Asset 삭제 (단일 트랜잭션)
   - Metadata DB: Video 레코드 hard-delete
   - Object Storage: 원본 영상, 오디오, 키프레임 파일 삭제 (비동기, 메인 서비스와 분리하여 처리)
6. DELETE_REQUEST 처리 시 대상 Video 레코드가 이미 존재하지 않으면 중복 삭제로 간주하고 성공으로 처리한다. 이 경우에도 메시지는 Ack되며 오류로 취급하지 않는다.

**출력**
- 이벤트: DELETE_REQUEST → Message Broker 발행

**저장 위치**
| 데이터 | 저장소 |
|--------|--------|
| Video.status=DELETING (즉시 전이) | Metadata DB |
| (삭제 완료 후) Video, Chunk, TranscriptSegment, Asset, VectorIndexEntry 레코드 소멸 | Metadata DB |
| (비동기) 원본 영상, 오디오, 키프레임 파일 소멸 | Object Storage |

## 2.6 Feedback 수집

**발생 시점**
- 사용자가 검색 결과에 대해 좋아요/싫어요를 누를 때

**처리**
1. Client가 Search Service 응답의 `chunks`에서 전체 `chunk_id` 목록을 `topk_ids`로, `used=true`인 항목의 `chunk_id` 목록을 `used_ids`로 파생한 뒤 `req_id`와 함께 Reverse Proxy를 거쳐 Core API Server에 피드백을 전송한다.
2. Core API Server가 해당 시점의 `query_text`, `topk_ids`, `used_ids`를 함께 수집하여 검색 응답 단위로 저장한다.

**저장 위치**
| 데이터 | 저장소 |
|--------|--------|
| user_id, req_id, query_text, rating, topk_ids, used_ids, created_at | Metadata DB |

## 2.7 모델 재학습 및 배포

**발생 시점**
- Pipeline Controller가 재학습 작업을 트리거할 때

**처리**
1. Pipeline Controller가 Message Broker에 재학습 작업 큐 발행 (TRAINING_REQUEST)
2. Model Training Worker가 Metadata DB에서 like 피드백 로그 로드
3. used_ids를 positive, topk 중 used가 아닌 chunk를 negative로 분류하여 (query, positive_chunk, negative_chunk) 형태의 JSONL로 전처리 후 Object Storage에 저장
4. Model Training Worker가 Managed ML Platform에 파인튜닝 작업(Job)을 요청하여 외부 GPU 클러스터에서 파인튜닝 수행
5. 학습 완료 후 반환된 평가 지표가 기준을 통과하면, Model Training Worker가 모델 파일 + 버전 메타데이터를 배포 가능한 아티팩트로 패키징한다.
6. 운영자가 선택한 모델 아티팩트를 Managed Embedding Endpoint 배포 경로에 반영하고, 새 프로세스를 기동하여 서빙한다.

**저장 위치**
| 데이터 | 저장소 |
|--------|--------|
| 전처리된 학습 데이터 (JSONL) | Object Storage |
| 모델 파일 + 버전 메타데이터 | Model Artifact Files |

# 3. Data Model (high level)

## 3.0 User
서비스 사용자 계정 정보
- id: 사용자 고유 ID
- email: 이메일
- created_at: 가입 시각

## 3.1 Video
영상 업로드 시 생성되는 기본 메타데이터 단위
- id: 영상 고유 ID
- user_id: 업로드한 사용자 ID
- title: 영상 제목
- category: 도메인 카테고리 (일반/IT/의학/법률)
- input_type: 입력 방식 (LOCAL_FILE / EXTERNAL_URL)
- source_url: 외부 URL 입력 시 원본 URL (Local File의 경우 null)
- storage_path: Object Storage 내 영상 파일 경로
- status: 처리 상태 (PENDING / UPLOADED / PROCESSING / READY / FAILED / DELETING)
- failed_stage: 실패 시 어느 범주의 단계에서 실패했는지 나타내는 분류값. 재시도 시 재개 판단 근거로 활용한다. (예: DOWNLOAD / EXTRACT / STT / CHUNKING / EMBEDDING / VECTOR_UPSERT)
- created_at: 업로드 요청 시각
- updated_at: 상태 변경 시각

## 3.2 Asset
Media Processing 단계에서 생성되는 파일들의 스토리지 포인터
- id: 자산 고유 ID
- video_id: 연관된 영상 ID (Video 참조)
- type: 파일 유형 (AUDIO / KEYFRAME)
- storage_path: Object Storage 내 파일 경로
- timestamp_ms: 키프레임의 경우 추출된 영상 내 시각 (AUDIO의 경우 null)
- format: 파일 포맷 (mp3, wav, jpg 등)
- created_at: 생성 시각

## 3.3 TranscriptSegment
STT 결과물로 생성되는 시간 구간 단위의 원본 텍스트
- id: 세그먼트 고유 ID
- video_id: 연관된 영상 ID (Video 참조)
- start_ms: 구간 시작 시각 (밀리초)
- end_ms: 구간 종료 시각 (밀리초)
- text: 해당 구간의 원본 텍스트
- stt_model_version: 생성에 사용된 STT 모델 버전
- created_at: 생성 시각

## 3.4 Chunk
시맨틱 청킹 결과물로 하이브리드 검색의 기본 단위
- id: 청크 고유 ID
- video_id: 연관된 영상 ID (Video 참조)
- start_ms: 구간 시작 시각 (밀리초)
- end_ms: 구간 종료 시각 (밀리초)
- text: 청크 텍스트 (STT+청킹 결과 원본; 전문 검색 인덱싱 기준)
- enriched_text: 검색용 합성 텍스트 (chunk_text + visual_caption + ocr_text + scene_tags). caption/OCR 없을 경우 text와 동일값.
- visual_caption: 대표 키프레임 기반 Vision caption 원문 (없을 경우 빈 문자열)
- ocr_text: 대표 키프레임 OCR 추출 원문 (없을 경우 빈 문자열)
- scene_tags: 대표 키프레임 scene tag 문자열 (없을 경우 빈 문자열)
- keyframe_asset_id: 매핑된 키프레임 (Asset 참조, 없을 경우 null)
- chunking_version: 청킹 방식 버전
- embedding_model_version: 임베딩 생성에 사용된 모델 버전
- created_at: 생성 시각

## 3.5 Feedback
사용자의 명시적 피드백 및 모델 학습에 필요한 컨텍스트 로그
- id: 피드백 고유 ID
- user_id: 피드백을 남긴 사용자 ID (User 참조)
- req_id: 검색 응답 고유 ID. 피드백의 귀속 단위이며 Search Service가 응답마다 생성한다. 별도 검색 응답 레코드의 FK가 아니라 클라이언트-Search-Core 간 상관관계용 opaque ID로 사용한다.
- query_text: 피드백 시점의 질의 텍스트
- rating: 평가 (LIKE / DISLIKE)
- topk_ids: SOT 게이트를 통과해 실제 응답 생성에 사용된 최종 청크 ID 목록 (relevance 순)
- used_ids: LLM이 structured `used_refs`를 통해 실제 참조했다고 보고한 최종 청크 ID 목록
- created_at: 피드백 시각

## 3.6 VectorIndexEntry (Derived; Vector Store)
- chunk_id: Chunk 고유 ID (SOT의 Chunk.id와 동일 키)
- user_id: 테넌시 필터용
- video_id: 스코프 필터용
- embedding_vector: 임베딩 벡터
- embedding_model_version: 모델 버전
- created_at: 적재 시각

## 3.7 Async Message Contract
비동기 파이프라인에서 사용되는 공통 메시지 규격. 페이로드에는 상태 조회를 위한 최소한의 식별자(video_id 등)만 포함하며, 상세 데이터는 Worker가 Metadata DB를 직접 조회하여 획득한다.

**공통 Envelope (MessageEnvelope)**
- message_type: 메시지 종류 (PREPROCESS_REQUEST / TRAINING_REQUEST 등)
- payload_version: 스키마 버전 (예: v1)
- trace_id: 분산 추적 및 로그 상관관계 ID (모든 내부 호출 및 큐 메시지는 동일 trace_id 상속)
- attempt: 재시도 횟수 (멱등/재처리 판단에 사용. 최초 1, 재발행 시 +1)
- video_id: 대상 영상 ID (파이프라인 메시지의 기본 키)
- issued_at: 발행 시각

**메시지 타입별 제약사항 (Payload)**
- `PREPROCESS_REQUEST`: Payload 추가 필드 없음. 워커 통합으로 인해 단일 큐로 파이프라인 전체(다운로드~추출~임베딩)를 트리거함.
- `DELETE_REQUEST`: Payload 추가 필드 없음. Worker가 video_id로 DB를 조회하여 storage_path 등 삭제 대상 정보를 확인하고 연쇄 삭제를 수행함.
- `TRAINING_REQUEST`: Payload 추가 필드 없음. 학습 대상 및 범위는 DB의 피드백 로그를 기준으로 워커가 자체 조회함.

---

