# 1. Architecture Overview

## 1.1 주요 컴포넌트

### 클라이언트 서브시스템 (Client Subsystem)

**목적**: 사용자의 디바이스(브라우저)에서 실행되며, 백엔드 시스템과 상호작용할 수 있는 시각적 인터페이스를 제공한다.

#### Client (User Web UI)

1. 업로드 및 검색 UI 제공: 로컬 파일 업로드 또는 외부 URL 입력, 자연어 질의 입력, 검색 결과(근거 구간/타임스탬프) 표시
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
6. 피드백 수집(Feedback Ingestion): 검색 결과에 대한 좋아요/싫어요와 함께 해당 시점의 query_text, topk_chunk_ids, cited_chunk_ids를 같이 저장
7. 비동기 작업 요청: 영상 처리처럼 시간이 오래 걸리는 작업은 직접 처리하지 않고 메시지 브로커로 전달하여, 즉시 “요청 접수(Accepted)” 응답을 반환한다.

#### Search Service

1. 검색 전담 처리: 자연어 질의를 수신하여 하이브리드 검색 파이프라인(임베딩 변환 및 DB/Vector 조회)을 즉시 시작한다.
2. 하이브리드 검색 오케스트레이션 (Search Orchestrator):
   - 키워드 후보는 Metadata DB(FTS)에서, 벡터 후보는 Vector Store(ANN)에서 각각 생성한다.
   - 두 후보를 RRF(Reciprocal Rank Fusion) 등으로 Search Service에서 병합하여 최종 Top-K를 만든다.
   - 최종 응답 전에 Metadata DB(SOT)에서 최종 서빙 검증을 수행하여 (권한/존재 여부(삭제=hard delete)/READY 상태) 기준을 통과한 Chunk만 반환한다.
     - 삭제는 hard delete이며, “삭제” 검증은 Chunk/Video 레코드의 존재 여부로 판단한다.
3. LLM 컨텍스트 주입: 추출된 청크들을 LLM(Answer Generator) 프롬프트에 조립하여 최종 답변을 생성하며, 피드백 학습에 쓰일 근거 ID(topk_chunk_ids, cited_chunk_ids)와 타임스탬프 레퍼런스를 함께 반환한다.
4. 인증 및 테넌시 강제(AuthN/AuthZ): 전달받은 Access Token(JWT)을 직접 검증하여 requester_user_id를 추출한다. 이를 기준으로 검색 대상 범위를 제한하고, Metadata DB(FTS) 및 Vector Store(ANN) 조회 단계 모두에서 사용자 테넌트 필터를 반드시 강제한다.


---

### 비동기 파이프라인 서브시스템 (Async Pipeline Subsystem)

**목적:** API 서버와 무거운 데이터 처리/학습 작업을 비동기적으로 분리(Decoupling)하여 시스템의 응답성을 유지하고, 메시지 큐 기반의 유연한 확장을 통해 대용량 미디어 및 AI 연산을 안정적으로 처리한다.

#### Message Broker

1. 작업 대기열 관리: API 서버로부터 전달받은 '영상 처리 요청'을 큐(Queue)에 적재하여, Worker가 처리 가능한 시점에 작업을 가져가도록 한다.
2. 워크로드 격리: 모델 학습(Training) 큐를 별도로 운영하여, 학습 부하가 업로드/검색 처리에 영향을 주지 않도록 분리한다.
3. 메시지 계약(Message Contract): 파이프라인 메시지는 공통 Envelope + 메시지별 Payload로 구성하며, 최소 필드/스키마는 Data Model의 “Message Contract(3.7~)” 정의를 따른다.

#### Media Processor

1. URL 영상 저장 후 전처리: PREPROCESS_REQUEST 수신 시 대상 영상이 외부 URL(EXTERNAL_URL) 인입인 경우, 영상을 먼저 다운로드하여 스토리지에 저장한 후 상태를 UPLOADED로 갱신한다. 
   단, 이미 스토리지에 해당 파일이 존재하면 다운로드를 생략하는 멱등성(Idempotency)을 보장한다.
2. 오디오 추출: 저장된 원본 영상에서 음성 파일을 추출하여 스토리지에 저장하고, STT 모델이 음성→텍스트 변환을 수행할 수 있도록 준비한다.
3. 키프레임 추출: 영상의 화면 전환(슬라이드 변경 등)이 발생하는 시점을 감지하여 중복된 이미지를 제외하고, 시각적 정보량이 변하는 구간의 대표 이미지와 그 타임스탬프만 선별적으로 추출하여 저장한다
4. 전처리 완료 이벤트 발행:
   - 오디오/키프레임 Asset(경로/메타데이터)을 Metadata DB에 저장한 뒤,
   - Message Broker에 전처리 완료 이벤트(PREPROCESS_COMPLETED)를 발행한다.
   - 이벤트 최소 payload: {message_type, payload_version, trace_id, attempt, video_id}
5. 멱등 처리(재시도/중복 메시지): PREPROCESS_REQUEST가 중복 도착하더라도 앞단의 다운로드(존재 시 생략) 및 뒷단의 Asset 저장 로직이 중복 수행되지 않도록 검증하여 안전한 재시도(Retry)를 보장한다.

#### AI Pipeline Worker

1. STT 추론 요청 및 스크립트 적재:
   - 전처리 완료 이벤트를 수신하면, Metadata DB의 Asset을 video_id로 조회하여 오디오/키프레임 storage_path를 획득한다.
   - 획득한 오디오를 Object Storage에서 로드하여 AI Model Gateway에 STT 추론을 요청하고, 반환된 스크립트(타임스탬프 포함)를 DB에 저장한다.
2. 시맨틱 청킹 (Semantic Chunking): 변환된 텍스트(Script)를 받아 문맥 단위로 분할하여 검색 가능한 청크(Chunk)를 생성한다.
3. 멀티모달 매핑 (Alignment): 생성된 텍스트 청크(예: 00:15 ~ 00:45)에 해당하는 구간에서 가장 적절한 키프레임을 Media Processor가 추출해둔 이미지 중에서 찾아 매핑
4. 임베딩 추론 요청 및 검색 색인 구축:
   - 전체 대본(스크립트)과 검색용 텍스트 조각(청크)은 Metadata DB에 저장한다. 이때 각 데이터의 실제 텍스트, 등장 시간(타임스탬프), 그리고 짝지어진 키프레임 이미지 주소(참조 ID)를 함께 기록한다.
   - 임베딩 벡터는 Vector Store(ANN)에 적재(Upsert)한다. (검색 인덱스는 Projection)
   - 두 저장소 반영이 완료된 시점에 Video.status를 READY로 갱신하여 “검색 가능 상태”를 보장한다.
5. 처리 안정성 및 멱등성 보장: 작업 실패 시 기존 산출물을 보존하며, 재처리 요청 시 failed_stage 상태를 확인하여 실패 지점부터 안전하게 이어할 수 있도록(Resume) 부분 실패 대응 및 데이터 정합성을 유지한다.

#### Model Training Worker

1. 데이터셋 전처리: 사용자 피드백 및 관리자가 확정한 학습용 데이터셋을 로드하여 학습 가능한 포맷으로 변환한다.
2. 모델 성능 개선: 피드백 기반 데이터셋을 활용해 임베딩 모델을 개선하고, 필요 시 기존 벡터 데이터를 재색인한다.
3. 모델 파인 튜닝: 구축된 특정 도메인의 데이터셋을 활용해 범용 임베딩 모델을 특정 도메인(법률, 의료 등) 데이터에 맞춰 재학습(Fine-tuning)한다.
4. 자동 평가(Auto-Evaluation): 후보 모델과 현재 운영 모델의 성능을 비교 평가하고, 개선이 검증된 경우에만 배포 가능한 상태로 등록한다.
5. 메시지 소비: TRAINING_REQUEST를 소비하여 학습/평가/배포 파이프라인을 수행한다. (상세 스키마는 Data Model 참조)
---

### AI 추론 서브시스템 (AI Inference Subsystem)

**목적:** 비즈니스 로직(Worker/API)과 AI 모델 실행 환경(Infra)을 분리하여 확장성과 유연성을 확보한다.

#### AI Model Gateway

1. 추론 인터페이스 단일화: Worker나 API Server가 로컬 모델(Local GPU)을 쓰는지 외부 API(Vertex AI, OpenAI)를 쓰는지 알 필요 없도록 통일된 API(예: transcribe, embed, generate_answer)를 제공한다.
2. 리소스 및 비용 최적화: 로컬 모델 서빙 시 배치(Batch) 처리를 통해 GPU 효율을 높이거나, 외부 API 호출 시 속도 제한(Rate Limiting) 및 키 관리를 수행한다.
3. LLM 연동 (RAG Generation): 프롬프트를 LLM에 전달하고 답변을 반환. 이때 답변에 근거 chunk_id를 포함하도록 프롬프트를 규칙화하여 cited_chunk_ids 추출이 가능하도록 한다 

#### Managed Embedding Endpoint (자체 호스팅 모델)

1. 임베딩 추론 처리: Model Registry에서 로드한 가장 최신 버전의 임베딩 모델을 통해 텍스트/이미지를 벡터로 변환
2. 오토스케일링: 트래픽 증감에 따라 GPU 인스턴스를 동적으로 확장/축소

#### External AI Adapters (외부 API)

1. STT 및 LLM 연동: OpenAI, Gemini 등 외부 상용 API와의 통신 및 에러 핸들링 (Retry, 서킷 브레이커 등) 담당

---

### 데이터 저장소 (Data & Storage Subsystem)

**목적:** 데이터의 특성에 따라 저장소를 분리하여 성능/내구성/검색 효율을 확보

#### Object Storage

1. 대용량 파일 저장: 원본 영상, 추출된 오디오, 키프레임 이미지 등 대용량 파일을 저장한다.

#### Metadata DB (Source of Truth; RDB)

1. 정합성 보장(SOT): 사용자, 영상 메타데이터, 상태(Status), Transcript/Chunk(텍스트/타임스탬프/참조), 피드백을 ACID 트랜잭션으로 저장한다.
2. 키워드 검색(FTS): Chunk.text에 대한 FTS 인덱스를 운영하여 키워드 후보를 생성한다. (초기 구성: RDB 내 FTS)
3. 최종 서빙 검증(SOT Validation): 검색 결과로 반환되기 전, 권한/존재 여부(삭제=hard delete)/상태(READY) 기준으로 노출 가능한 Chunk만 최종 확정하는 기준 저장소로 동작한다.

#### Vector Store (ANN Index; Derived Projection)

1. 벡터 저장(파생 인덱스): Chunk 단위 임베딩 벡터를 저장하며, 검색 시 테넌시 필터를 적용할 수 있도록 최소 메타데이터(user_id, video_id 등)를 함께 보유한다.
2. 최종 일관성: Vector Store는 Metadata DB로부터 파생된 Projection이며, 부분 실패/지연이 발생할 수 있다. 사용자 노출 정합성은 Metadata DB의 상태(READY) 및 최종 서빙 검증(SOT Validation)으로 보장한다.


#### Model Registry / Artifact Store

1. 모델 버전 관리: 임베딩 모델 파일과 버전 메타데이터를 저장하고, 최신 모델을 AI Model Gateway에 자동 로드한다.

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

* 오케스트레이션 및 정합성 관리: 파이프라인 상태를 기준으로 단계 실행 순서, 재시도를 관리한다. 특히 수동 삭제 명령 시 메타데이터 DB와 Object
  Storage에 유령 데이터가 남지 않도록 연쇄 삭제(Cascade Delete) 워크플로우를 보장한다.
* 모델 교체 및 재색인 트리거: 임베딩/STT 모델 버전 변경 시, 기존 데이터를 새로운 모델로 다시 벡터화(재색인)하는 작업을 트리거한다.
* 배포/롤백 제어: 평가를 통과한 모델을 배포하고, 문제 발생 시 이전 버전으로 즉시 롤백할 수 있도록 제어 로직을 제공한다.

---

## 1.2 컴포넌트 간 데이터 흐름

비디오 업로드부터 검색/응답까지의 핵심 데이터 이동 경로를 요약
메타데이터 DB는 전체 파이프라인의 상태(Status)를 관리한다.

**Status 전이:** `PENDING → UPLOADED → PROCESSING → READY (또는 FAILED)`

### 1. Video Ingest
* Client → Reverse Proxy → API Server → Metadata DB: 영상 메타데이터(제목, 카테고리, 입력 방식)를 저장하여 비디오 레코드 생성 (status=PENDING)

* (Local File) API Server → Client: Presigned URL + video_id
* (Local File) Client → Object Storage: 영상 원본 업로드
* (Local File) Client → Reverse Proxy → API Server: 업로드 완료 신호(video_id)
* (Local File) API Server → Metadata DB: status=UPLOADED 갱신
* (Local File) API Server → Message Broker: 전처리 작업 요청 발행 (PREPROCESS_REQUEST)

* (External URL) API Server → Message Broker: 전처리 작업 및 다운로드 요청 바로 발행 (PREPROCESS_REQUEST)
      (참고: External URL의 다운로드 및 UPLOADED 상태 갱신은 2. Media Preprocessing 단계 내에서 통합 수행됨)

### 2. Media Preprocessing
* Message Broker → Media Processor: 전처리 작업 요청(PREPROCESS_REQUEST) 전달
* Media Processor → Metadata DB: Video 정보 로드 및 멱등성(기존 파일 존재 여부) 체크
* (External URL 업로드 시) Media Processor → 외부 소스(YouTube 등): 영상 다운로드
* (External URL 업로드 시) Media Processor → Object Storage: 다운로드한 원본 영상을 내부 권한(SDK)으로 직접 저장
* (External URL 업로드 시) Media Processor → Metadata DB: status=UPLOADED 갱신
* Media Processor → Metadata DB: 본격적인 추출 시작 전 status=PROCESSING 갱신 (Local File 업로드인 경우 여기서부터 공통 수행)
* Object Storage → Media Processor: 영상 원본 파일 로드 (단, 외부 URL 인입 직후에는 다운로드된 로컬 임시 파일을 재사용하여 I/O 최적화)
* Media Processor → Object Storage: 추출된 오디오 파일, 키프레임 이미지 저장
* Media Processor → Metadata DB: 오디오/키프레임 경로 및 메타데이터 저장
* Media Processor → Message Broker: 전처리 완료 이벤트 발행 (PREPROCESS_COMPLETED)

### 3. AI Analysis & Indexing
* Message Broker → AI Pipeline Worker: 전처리 완료 이벤트(PREPROCESS_COMPLETED) 소비
* AI Pipeline Worker → Metadata DB: Asset 조회(video_id) → 오디오/키프레임 storage_path 획득
* Object Storage → AI Pipeline Worker: 오디오 파일, 키프레임 이미지
* AI Pipeline Worker → AI Model Gateway: 오디오 파일 전송 (STT 추론 표준 요청)
* AI Model Gateway → External AI Adapters: 외부 STT API(OpenAI 등) 호출 및 예외 처리
* External AI Adapters → AI Model Gateway: 원시(Raw) 텍스트 응답 수신
* AI Model Gateway → AI Pipeline Worker: 스크립트 + 타임스탬프 (내부 표준 포맷으로 반환)
* AI Pipeline Worker → AI Model Gateway: 텍스트 청크 + 키프레임 전송 (임베딩 추론 표준 요청)
* AI Model Gateway → Managed Embedding Endpoint: 자체 호스팅 모델에 벡터화 요청
* Managed Embedding Endpoint → AI Model Gateway: 임베딩 벡터 반환
* AI Model Gateway → AI Pipeline Worker: 임베딩 벡터 (내부 표준 포맷으로 반환)
* AI Pipeline Worker → Metadata DB: 스크립트/청크 텍스트/타임스탬프/참조 적재 (SOT)
* AI Pipeline Worker → Vector Store: 청크 임베딩 벡터 적재(Upsert) (Projection)
* AI Pipeline Worker → Metadata DB: 인덱스 반영 완료 후 status=READY 갱신 (실패 시 FAILED)

### 4. Search & RAG Serving
* Client → Reverse Proxy: 자연어 질의 + scope + Authorization(Bearer token)
* Reverse Proxy → Search Service: 단순 경로 라우팅 (Authorization 헤더 원본 유지)
* Search Service: JWT 검증(서명 및 만료) 수행 후 claim에서 requester_user_id 추출 (테넌시 필터에 사용)

* Search Service → AI Model Gateway: 질의 임베딩 변환 요청
* AI Model Gateway → Managed Embedding Endpoint: 임베딩 요청
* Managed Embedding Endpoint → AI Model Gateway: 질의 임베딩 벡터 반환
* AI Model Gateway → Search Service: 질의 임베딩 벡터 (표준 포맷)
* Search Service → Metadata DB(FTS): 키워드 후보 Top-K 조회 (테넌시/스코프 적용)
* Search Service → Vector Store(ANN): 벡터 후보 Top-K 조회 (테넌시/스코프 적용)
* Search Service: 후보 병합(RRF) 후 최종 Top-K 결정
* Search Service → Metadata DB(SOT): 서빙 게이트 검증(권한/존재 여부(삭제=hard delete)/READY) + 컨텍스트(텍스트/타임스탬프) 로드
* Metadata DB → Search Service: 최종 Top-K 컨텍스트(텍스트/타임스탬프)
* Search Service → AI Model Gateway: Top-K 컨텍스트 + 질의 (LLM 요청)
* AI Model Gateway → External AI Adapters: 외부 LLM API 호출 및 예외 처리
* External AI Adapters → AI Model Gateway: 생성된 답변 수신
* AI Model Gateway → Search Service: 생성된 답변 + cited_chunk_ids (표준 포맷)
* Search Service → Client: 생성된 답변 + 근거 타임스탬프 + topk_chunk_ids + cited_chunk_ids

### 5. Feedback & Model Improvement
* Client → Reverse Proxy → Core API Server: 명시적 피드백 (좋아요/싫어요)
* Core API Server → Metadata DB: 피드백 + query_text + topk_chunk_ids + cited_chunk_ids 저장
* Pipeline Controller → Message Broker: 모델 재학습 작업 큐 발행 (TRAINING_REQUEST)
* Message Broker → Model Training Worker: 재학습 작업 소비
* Metadata DB → Model Training Worker: like 피드백 로그 로드
* Model Training Worker → Object Storage: 전처리된 학습 데이터 (JSONL) 저장
* Model Training Worker → Managed ML Platform (Vertex AI, SageMaker 등): 학습 데이터 경로를 전달하여 GPU 기반 파인튜닝 작업(Training Job) 트리거
* Managed ML Platform → Model Training Worker: 학습 완료 파이프라인 콜백 및 자동 평가(Auto-Evaluation) 지표 반환
* Model Training Worker → Model Registry: 검증을 통과한 모델 가중치(Artifact) + 버전 메타데이터 등록
* Model Registry → Managed Embedding Endpoint: 최신 모델 자동 풀링 및 무중단 배포
---

# 2 Data Flow

## 2.1 Video Ingest (Local File)

**입력**
- 주체: Client
- 데이터: 영상 파일 (binary), 메타데이터 (제목, 카테고리)

**처리**
1. Core API Server가 고유 식별자(UUID 등)를 직접 생성하여 video_id를 할당한다.
2. 할당된 video_id를 기반으로 Object Storage에 저장될 객체 키(storage_path)를 결정하고, 이를 포함해 클라이언트가 사용할 Presigned URL을 발급한다.
3. Core API Server가 메타데이터(제목, 카테고리 등)와 확정된 storage_path를 한 번의 트랜잭션으로 Metadata DB에 저장한다. (status=PENDING)
4. Client가 Presigned URL을 통해 Object Storage에 직접 영상 파일 업로드
5. Client가 Reverse Proxy를 거쳐 Core API Server에 업로드 완료 신호 전송
6. Core API Server가 status를 UPLOADED로 갱신 후 Message Broker에 전처리 작업 큐 발행 (PREPROCESS_REQUEST)

**출력**
- Presigned URL + 영상 고유 ID → Client 반환
- 전처리 작업 메시지(PREPROCESS_REQUEST) → Message Broker 발행

**저장 위치**
| 데이터 | 저장소 |
|--------|--------|
| 영상 원본 파일 | Object Storage |
| 영상 메타데이터 (제목, 카테고리, 업로더, status=UPLOADED) | Metadata DB |

## 2.2 Video Ingest (External URL)

**입력**
- 주체: Client
- 데이터: 외부 영상 URL, 메타데이터 (제목, 카테고리)

**처리**
   1. Core API Server가 고유 식별자(UUID 등)를 직접 생성하여 video_id를 할당하고, 향후 영상이 저장될 storage_path를 미리 결정한다.
   2. Core API Server가 메타데이터(제목, 카테고리, 원본 URL 등)와 예약된 storage_path를 한 번의 트랜잭션으로 Metadata DB에 저장한다. (status=PENDING)
   3. Core API Server가 곧바로 Message Broker에 전처리 작업 큐 발행(PREPROCESS_REQUEST)을 수행한다. (URL 정보는 Metadata DB 조회를 통해 Media Processor로 전달됨)

**출력**
- 전처리 작업 메시지(PREPROCESS_REQUEST) → Message Broker 발행

**저장 위치**
| 데이터 | 저장소 |
|--------|--------|
| 영상 원본 파일 | Object Storage |
| 영상 메타데이터 (제목, 카테고리, 업로더, source_url, status=UPLOADED) | Metadata DB |

## 2.3 Media Preprocessing

**입력**
- 주체: Media Processor (Message Broker로부터 PREPROCESS_REQUEST 소비)
- 데이터: 영상 원본 파일 (Object Storage)

**처리**
1. Media Processor가 PREPROCESS_REQUEST를 수신하고 Metadata DB에서 Video 정보를 로드하여 상태 기반 멱등성 체크를 수행한다. (이미 처리 완료(READY)된 영상인지 확인하여 불필요한 중복 작업을 방지)
2. (External URL 인입인 경우 한정) Object Storage에 예약된 storage_path에 파일이 물리적으로 존재하는지 확인한다(다운로드 멱등성 체크). 파일이 없다면 source_url에서 영상을 다운로드하여 저장하고, Metadata
   DB의 상태를 UPLOADED로 갱신한다.
3. Media Processor가 본격적인 추출 작업 시작을 알리기 위해 Metadata DB의 Video.status를 PROCESSING으로 갱신한다. (Local File 인입인 경우 1번 수행 후 바로 이 단계부터 공통 수행)
4. Object Storage에서 영상 원본 파일 로드
   - URL 인입 직후: 2번 과정에서 생성된 로컬 임시 파일을 즉시 사용한다.
   - 로컬 업로드 또는 재처리 시: Object Storage에서 영상 원본 파일을 로드한다.
5. 영상에서 오디오 추출
6. 영상에서 키프레임 추출 (화면 전환 감지 기준)

**출력**
- 오디오 파일 → Object Storage 저장
- 키프레임 이미지 → Object Storage 저장
- 오디오/키프레임 경로 및 메타데이터 → Metadata DB 저장
- 전처리 완료 이벤트(PREPROCESS_COMPLETED) → Message Broker 발행

**저장 위치**
| 데이터 | 저장소 |
|--------|--------|
| 오디오 파일 | Object Storage |
| 키프레임 이미지 | Object Storage |
| 오디오/키프레임 경로, status=PROCESSING | Metadata DB |


## 2.4 AI Analysis & Indexing

**입력**
- 주체: AI Pipeline Worker (Message Broker로부터 PREPROCESS_COMPLETED 소비)
- 데이터: video_id(이벤트), Asset 메타데이터(Metadata DB), 오디오/키프레임(Object Storage)

**처리**
1. Worker가 Metadata DB에서 video_id로 Asset과 Video를 조회하여 오디오/키프레임 storage_path 및 Video.user_id를 획득한다.
2. 오디오 파일을 처리 단위로 분할하여 AI Model Gateway에 전달 → Gateway가 External AI Adapters를 통해 외부 STT API 호출 → 반환된 결과를 Gateway가 표준화하여 스크립트+타임스탬프로 Worker에 전달
3. Worker가 전체 스크립트를 문맥 단위로 시맨틱 청킹
4. 텍스트 청크 타임스탬프 구간에 해당하는 키프레임 매핑
5. 텍스트 청크 + 키프레임을 AI Model Gateway에 전달 → Gateway가 Managed Embedding Endpoint(자체 배포 모델)로 임베딩 요청 → 벡터화된 결과 반환
6. 스크립트/청크(텍스트, 타임스탬프, 참조)는 Metadata DB(SOT)에 적재하고,
   임베딩 벡터는 Vector Store(ANN)에 적재(Upsert)한다.
7. 두 저장소 반영이 완료되면 Metadata DB에서 status=READY로 갱신한다.

**출력**
- 전체 스크립트(세그먼트) + 타임스탬프 → Metadata DB(SOT) 트랜잭션 적재
- 청크 텍스트 + 타임스탬프 + 키프레임 참조(및 chunk_id) → Metadata DB(SOT) 트랜잭션 적재
- 청크 임베딩 벡터(+ 필터용 최소 메타데이터: chunk_id, video_id, user_id(Video.user_id) 등) → Vector DB에 Upsert (검색용 인덱스 구축)
- (상태) 두 저장소 반영 완료 후 Metadata DB에서 status=READY 갱신 (실패 시 FAILED 및 failed_stage 기록)

**저장 위치**
| 데이터 | 저장소 |
|--------|--------|
| 전체 스크립트, 타임스탬프 | Metadata DB |
| 청크 텍스트, 타임스탬프, 키프레임 참조, (chunk_id 등) | Metadata DB |
| 청크 임베딩 벡터 (+ 필터용 최소 메타데이터) | Vector Store |
| status=READY (실패 시 FAILED + failed_stage) | Metadata DB |

## 2.5 Search & RAG Serving

**입력**
- 주체: Client
- 데이터: 자연어 질의 텍스트, Authorization 토큰, scope(검색 범위; 예: {all_my_videos:true} / {video_ids:[...]} / {category:"IT"})

**처리**
1. Reverse Proxy가 요청을 수신하여 Authorization 헤더를 포함한 원본 요청을 그대로 Search Service로 전달
2. Search Service가 내부 미들웨어를 통해 JWT를 직접 검증하고, claim에서 requester_user_id를 추출하여 테넌시 필터에 사용
3. 질의 텍스트를 AI Model Gateway를 통해 임베딩 벡터로 변환 (Gateway → Managed Embedding Endpoint)
4. Search Service가 Metadata DB(FTS)에서 키워드 후보 Top-K를 조회 (테넌시/스코프 적용)
5. Search Service가 Vector Store(ANN) 에서 벡터 후보 Top-K를 조회 (테넌시/스코프 적용)
6. Search Service가 키워드/벡터 후보를 병합(RRF)하여 최종 Top-K 후보를 결정
7. Search Service가 Metadata DB(SOT) 를 “서빙 게이트”로 조회하여 (권한/존재 여부(삭제=hard delete)/READY 상태) 검증을 수행하고, 최종 컨텍스트(청크 텍스트/타임스탬프)를 로드
8. Search Service가 Top-K 컨텍스트 + 질의를 AI Model Gateway에 전달 → LLM이 최종 답변(+ cited_chunk_ids) 생성 (Gateway → External AI Adapters)
9. Search Service가 생성된 답변 + 타임스탬프 + topk_chunk_ids + cited_chunk_ids를 Client에 최종 반환

**출력**
- 생성된 답변 + 근거 타임스탬프 + topk_chunk_ids + cited_chunk_ids → Client 반환

**저장 위치**

- 검색 응답은 실시간으로 생성 및 반환되며 별도로 영구 저장하지 않음. (단, 피드백 발생 시 2.7 절차에 따라 수집됨)

## 2.6 처리 실패 (FAILED)

**발생 시점**
- External URL Download, Media Preprocessing, AI Analysis & Indexing 단계에서 오류 발생 시

**처리**
1. 실패한 컴포넌트(API Server/Media Processor/AI Pipeline Worker)가 Metadata DB에 status=FAILED 및 failed_stage를 기록한다.
   - failed_stage 후보: DOWNLOAD / PREPROCESS / STT / CHUNKING / EMBEDDING / VECTOR_UPSERT
   - 예: Media Processor가 외부 URL 영상을 다운로드하던 중 네트워크 오류가 발생하면 failed_stage=DOWNLOAD, 다운로드는 성공(UPLOADED)했으나 오디오 추출 중 실패하면 failed_stage=PREPROCESS로 기록하여 실패 지점을 세분화한다.
2. 해당 시점까지 생성된 중간 산출물(오디오, 키프레임, 청크 등)은 삭제하지 않고 보존
3. 이후 재처리 요청 시, 동일한 메시지 큐(PREPROCESS_REQUEST 등)가 재발행되더라도 Worker는 DB의 failed_stage 상태나 스토리지 내 파일 존재 여부를 먼저 확인하는 멱등성(Idempotency) 로직을 통해, 이미 완료된 무거운 작업(예: 다운로드)은 건너뛰고(Skip) 실패한 단계(예: 오디오 추출)부터 안전하게 처리를 재개(Resume)한다.

**저장 위치**
| 데이터 | 저장소 |
|--------|--------|
| status=FAILED, failed_stage, error_message | Metadata DB |

## 2.7 Feedback 수집

**발생 시점**
- 사용자가 검색 결과에 대해 좋아요/싫어요를 누를 때

**처리**
1. Client가 Reverse Proxy를 거쳐 Core API Server에 피드백 전송
2. Core API Server가 해당 시점의 topk_chunk_ids, cited_chunk_ids를 함께 수집
3. Core API Server가 Metadata DB에 피드백 및 컨텍스트 로그 저장

**저장 위치**
| 데이터 | 저장소 |
|--------|--------|
| user_id, video_id, query_text, rating, topk_chunk_ids, cited_chunk_ids, created_at | Metadata DB |

---

## 2.8 모델 재학습 및 배포

**발생 시점**
- Pipeline Controller가 재학습 작업을 트리거할 때

**처리**
1. Pipeline Controller가 Message Broker에 재학습 작업 큐 발행 (TRAINING_REQUEST)
2. Model Training Worker가 Metadata DB에서 like 피드백 로그 로드
3. cited_chunk_ids를 positive, topk 중 cited가 아닌 chunk를 negative로 분류하여 (query, positive_chunk, negative_chunk) 형태의 JSONL로 전처리 후 Object Storage에 저장
4. Model Training Worker가 Managed ML Platform에 파인튜닝 작업(Job)을 요청하여 외부 GPU 클러스터에서 Sentence-Transformers 파인튜닝 수행
5. 학습 완료 후 반환된 평가 지표가 기준을 통과하면, Model Training Worker가 Model Registry에 모델 파일 + 버전 메타데이터 저장
6. Managed Embedding Endpoint가 Model Registry의 최신 버전을 감지하여 자동 로드 및 서빙

**저장 위치**
| 데이터 | 저장소 |
|--------|--------|
| 전처리된 학습 데이터 (JSONL) | Object Storage |
| 모델 파일 + 버전 메타데이터 | Model Registry |

# Data Model (hight level)

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
- status: 처리 상태 (PENDING / UPLOADED / PROCESSING / READY / FAILED)
- failed_stage: 실패 시 어느 단계에서 실패했는지 (예: DOWNLOAD / PREPROCESS / STT / CHUNKING / EMBEDDING / VECTOR_UPSERT)
- created_at: 업로드 요청 시각
- updated_at: 상태 변경 시각


## 3.2 Asset
Media Preprocessing 단계에서 생성되는 파일들의 스토리지 포인터

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
- text: 청크 텍스트 (전문 검색 인덱싱용)
- keyframe_asset_id: 매핑된 키프레임 (Asset 참조, 없을 경우 null)
- chunking_version: 청킹 방식 버전
- embedding_model_version: 임베딩 생성에 사용된 모델 버전
- created_at: 생성 시각


## 3.5 Feedback
사용자의 명시적 피드백 및 모델 학습에 필요한 컨텍스트 로그

- id: 피드백 고유 ID
- user_id: 피드백을 남긴 사용자 ID (User 참조)
- video_id: 대상 영상 ID (Video 참조)
- query_text: 피드백 시점의 질의 텍스트
- rating: 평가 (LIKE / DISLIKE)
- topk_chunk_ids: 검색 시 반환된 Top-K 청크 ID 목록
- cited_chunk_ids: LLM이 답변 생성에 실제로 사용한 청크 ID 목록
- created_at: 피드백 시각

## 3.6 VectorIndexEntry (Derived; Vector Store)

- chunk_id: Chunk 고유 ID (SOT의 Chunk.id와 동일 키)
- user_id: 테넌시 필터용
- video_id: 스코프 필터용
- embedding_vector: 임베딩 벡터
- embedding_model_version: 모델 버전
- created_at: 적재 시각

## 3.7 MessageEnvelope (Async Message Contract)

- message_type: 메시지 종류 (PREPROCESS_REQUEST / PREPROCESS_COMPLETED / TRAINING_REQUEST 등)
- payload_version: 스키마 버전 (예: v1)
- trace_id: 분산 추적/로그 상관관계 ID, trace_id는 Reverse Proxy가(없으면) 생성하며, 모든 내부 호출 및 큐 메시지는 동일 trace_id를 상속한다
- attempt: 재시도 횟수(멱등/재처리 판단에 사용), 최초 발행 attempt=1로 시작, 재발행 시 +1 추가
- video_id: 대상 영상 ID (파이프라인 메시지의 기본 키)
- issued_at: 발행 시각

> 원칙: 메시지 Payload에는 식별자(video_id 등)만 포함한다.
> 오디오/키프레임 경로 및 상세 메타데이터는 Worker가 Metadata DB의 Asset을 조회하여 획득한다.

## 3.8 PreprocessRequest (PREPROCESS_REQUEST)
- envelope: MessageEnvelope
- payload: (추가 필드 없음; video_id로 Video.storage_path를 Metadata DB에서 조회)

## 3.9 PreprocessCompleted (PREPROCESS_COMPLETED)
- envelope: MessageEnvelope
- payload: (추가 필드 없음; video_id로 Asset(AUDIO/KEYFRAME) storage_path를 Metadata DB에서 조회)

## 3.10 TrainingRequest (TRAINING_REQUEST)
- envelope: MessageEnvelope
- payload: (추가 필드 없음; 학습 대상/범위는 Metadata DB의 피드백 로그를 기준으로 조회)


# 4. Implementation Breakdown

## 4.1. Core Infrastructure & Security
- 통합 인증 미들웨어 구현: API Server 및 Search Service 전반에 적용될 JWT 검증 로직 및 requester_user_id 기반 테넌시(Tenancy) 필터링 로직 구현
- 데이터베이스 스키마 및 인덱스 설계: SOT 역할을 할 Metadata DB 테이블(Video, Asset, TranscriptSegment, Chunk, Feedback) 및 FTS(Full-Text Search) 인덱스 구성
- 공통 메시지 규격 및 큐 연동: 분산 추적(trace_id) 및 재시도 식별(attempt)을 포함한 MessageEnvelope 규격 정의 및 브로커 연동 로직 구현

## 4.2. Video Ingestion & Core API
- 영상 업로드 API 구현: Presigned URL 및 video_id 발급, Metadata DB 초기 상태(status=PENDING) 저장 로직 구현
- 비동기 전처리 트리거 구현: 로컬 영상 업로드 완료 수신 또는 외부 URL 입력 시 DB 상태를 갱신(UPLOADED 또는 PENDING)하고 PREPROCESS_REQUEST 메시지를 발행하는 로직 구현
- 피드백 수집 API 구현: 검색 응답에 대한 평가(LIKE/DISLIKE)와 검색 컨텍스트(topk_chunk_ids, cited_chunk_ids)를 수집하여 DB에 저장하는 로직 구현

## 4.3. Media Preprocessing Pipeline
- 멱등성 보장 다운로더 구현: 외부 URL 인입 시 스토리지 내 파일 존재 여부를 확인하여 중복 다운로드를 방지하고 안전하게 영상을 적재하는 로직 구현
- 미디어 추출 엔진 구현: 원본 영상에서 오디오를 추출하고, 화면 전환 감지(Scene Change Detection)를 통해 핵심 키프레임 이미지를 추출하는 로직 구현
- 전처리 완료 이벤트 발행: 추출된 Asset(오디오, 키프레임)을 스토리지에 저장하고, Metadata DB에 경로를 기록한 뒤 PREPROCESS_COMPLETED 이벤트를 발행하는 로직 구현

## 4.4. AI Analysis & Indexing
- 통합 추론 클라이언트 구현: AI Model Gateway를 통해 transcribe, embed 등 표준화된 추론 요청을 처리하는 API 연동 로직 구현
- 시맨틱 청킹 및 멀티모달 매핑: STT 스크립트를 문맥 단위로 분할(Chunking)하고, 타임스탬프를 기준으로 추출된 키프레임과 매핑하는 로직 구현
- 분산 저장소 적재 및 상태 전이 로직: Metadata DB(SOT)에 청크 데이터를 트랜잭션으로 적재하고, Vector Store(ANN)에 임베딩 벡터를 Upsert한 뒤 최종 상태를 READY로 갱신하는 로직 구현
- 부분 실패 및 재처리(Resume) 로직: 작업 실패 시 failed_stage를 DB에 기록하고, 재처리 시 완료된 단계를 건너뛰어 멱등성을 보장하는 로직 구현

## 4.5. Search & RAG Serving
- 하이브리드 검색 오케스트레이터 구현: Metadata DB의 키워드 검색(FTS)과 Vector Store의 벡터 검색(ANN) 결과를 RRF(Reciprocal Rank Fusion) 방식으로 병합하는 로직 구현
- 최종 서빙 검증(SOT Validation) 게이트웨이: 산출된 Top-K 청크에 대해 데이터 완전 삭제 여부(Hard delete), READY 상태, 테넌시 권한을 교차 검증하는 로직 구현
- RAG 답변 생성 및 근거 추출기 구현: 검증된 컨텍스트를 LLM 프롬프트에 주입하여 답변을 생성하고, 반환된 응답에서 근거 ID(cited_chunk_ids)를 추출하는 로직 구현

## 4.6. MLOps & Operations
- 학습 데이터 전처리 워커 구현: 수집된 사용자 피드백과 cited_chunk_ids를 기반으로 모델 파인튜닝을 위한 학습 데이터셋을 생성하고 스토리지에 적재하는 로직 구현
- 자동 평가 및 배포 파이프라인: Managed ML Platform에 파인튜닝 작업을 트리거하고, 평가를 통과한 모델 버전을 Model Registry에 등록 및 반영하는 워크플로우 구현
- 데이터 연쇄 삭제(Cascade Delete) 워크플로우: 영상 삭제 요청 시 Metadata DB(SOT)와 Object Storage, Vector Store 간의 유령 데이터(Orphaned Data)가 남지 않도록 정리하는 삭제 제어 로직 구현
- 관측성(Observability) 파이프라인 로깅: 모든 서비스 및 워커에서 trace_id 기반 로그를 기록하여 파이프라인 단계별 상태 및 소요 시간을 추적할 수 있도록 구현