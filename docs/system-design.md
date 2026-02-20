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

### 엣지 게이트웨이 서브시스템 (Edge Gateway Subsystem)

**목적**: 시스템 인프라(VPC)의 진입점으로서, 외부 트래픽을 수신하여 부하 분산을 수행하고 내부 백엔드 서비스로 안전하게 라우팅한다.

#### Edge Gateway (API Gateway / Load Balancer)

1. 요청 수신 및 라우팅: 외부 요청을 수신하고, 일반적인 파일 업로드 및 상태 조회 등은 API Server로, 검색 질의는 Search Service로 분리하여 라우팅한다.
2. 트래픽 분산 및 가용성: 동시 업로드 등 트래픽이 증가할 때 요청을 분산시켜 서비스 중단을 방지하고 가용성 목표(예: 99%)를 만족하도록 돕는다.

---

### 애플리케이션 서브시스템 (Application Subsystem)

**목적:** 게이트웨이를 통과한 클라이언트의 요청을 받아, 시스템의 핵심 도메인 로직(url 발급, 텍스트/벡터 검색, 메타데이터 저장, 피드백 수집 등)을 전담하여 처리하는 독립적인 백엔드 애플리케이션 그룹이다.

#### Core API Server

1. 사용자 요청 처리: 영상 업로드, 처리 상태 조회, 영상 목록 관리 등 클라이언트 요청을 처리한다.
2. presigned URL 발급: 권한 확인 후 업로드 가능한 URL과 영상 고유 id 를 발급
3. 메타데이터 및 초기 상태 저장: 영상에 대한 메타데이터(업로더, 영상이름,카테고리)와 초기 상태를 db에 저장
4. 업로드 완료 확인/처리 트리거: 클라이언트로 부터 업로드가 끝났다는 신호를 받아 파이프라인 작업을 큐에 넣고, 상태를 업데이트
5. 인증 및 인가: 사용자별 영상 접근 권한을 제어하여, 본인이 업로드한 영상과 결과에만 접근하도록 보안을 유지한다.
6. 피드백 수집(Feedback Ingestion): 검색 결과에 대한 좋아요/싫어요와 함께 해당 시점의 query_text, topk_chunk_ids, cited_chunk_ids를 같이 저장
7. 비동기 작업 요청: 영상 처리처럼 시간이 오래 걸리는 작업은 직접 처리하지 않고 메시지 브로커로 전달하여, 즉시 “요청 접수(Accepted)” 응답을 반환한다.

#### Search Service

1. 검색 전담 처리: 자연어 질의를 수신하고 캐시를 우선 확인한다.
2. 하이브리드 검색 및 병합 (Search Orchestrator): 캐시 미스 시, 통합 DB(Metadata & Search DB)를 통해 키워드 기반의 FTS와 의미 기반의 시맨틱(Vector) 검색을 동시에 수행한다. 이후 RRF(Reciprocal Rank Fusion) 알고리즘을 적용하여 양쪽의 검색 스코어를 병합하고 최종 Top-K 청크를 추출한다.
3. LLM 컨텍스트 주입: 추출된 청크들을 LLM(Answer Generator) 프롬프트에 조립하여 최종 답변을 생성하며, 피드백 학습에 쓰일 근거 ID(cited_chunk_ids)와 타임스탬프 레퍼런스를 함께 반환한다.
---

### 비동기 파이프라인 서브시스템 (Async Pipeline Subsystem)

**목적:** API 서버와 무거운 데이터 처리/학습 작업을 비동기적으로 분리(Decoupling)하여 시스템의 응답성을 유지하고, 메시지 큐 기반의 유연한 확장을 통해 대용량 미디어 및 AI 연산을 안정적으로 처리한다.

#### Message Broker

1. 작업 대기열 관리: API 서버로부터 전달받은 '영상 처리 요청'을 큐(Queue)에 적재하여, Worker가 처리 가능한 시점에 작업을 가져가도록 한다.
2. 워크로드 격리: 모델 학습(Training) 큐를 별도로 운영하여, 학습 부하가 업로드/검색 처리에 영향을 주지 않도록 분리한다.

#### Media Processor

1. URL 영상 저장: 외부 URL 입력의 경우 해당 url속 영상을 다운로드 하여 스토리지에 저장한다
2. 오디오 추출: 저장된 원본 영상에서 음성 파일을 추출하여 스토리지에 저장하고, STT 모델이 음성→텍스트 변환을 수행할 수 있도록 준비한다.
3. 키프레임 추출: 영상의 화면 전환(슬라이드 변경 등)이 발생하는 시점을 감지하여 중복된 이미지를 제외하고, 시각적 정보량이 변하는 구간의 대표 이미지와 그 타임스탬프만 선별적으로 추출하여 저장한다

#### AI Pipeline Worker

1. STT 추론 요청 및 스크립트 적재: Media Processor가 스토리지에 저장한 오디오를 로드하여 AI Model Gateway에 STT 추론을 요청하고, 반환된 스크립트(타임스탬프 포함)를 DB에 저장한다.
2. 시맨틱 청킹 (Semantic Chunking): 변환된 텍스트(Script)를 받아 문맥 단위로 분할하여 검색 가능한 청크(Chunk)를 생성한다.
3. 멀티모달 매핑 (Alignment): 생성된 텍스트 청크(예: 00:15 ~ 00:45)에 해당하는 구간에서 가장 적절한 키프레임을 Media Processor가 추출해둔 이미지 중에서 찾아 매핑
4. 임베딩 추론 요청 및 검색 색인 구축: Semantic Chunking/Alignment 결과(텍스트 청크 + 키프레임 참조)를 입력으로 AI Model Gateway에 임베딩 추론을 요청하고, 반환된 벡터와 텍스트, 메타데이터를 Metadata & Search DB에 단일 트랜잭션으로 적재한다.
5. 처리 안정성 및 멱등성 보장: 작업 실패 시 기존 산출물을 보존하며, 재처리 요청 시 failed_stage 상태를 확인하여 실패 지점부터 안전하게 이어할 수 있도록(Resume) 부분 실패 대응 및 데이터 정합성을 유지한다.

#### Model Training Worker

1. 데이터셋 전처리: 사용자 피드백 및 관리자가 확정한 학습용 데이터셋을 로드하여 학습 가능한 포맷으로 변환한다.
2. 모델 성능 개선: 피드백 기반 데이터셋을 활용해 임베딩 모델을 개선하고, 필요 시 기존 벡터 데이터를 재색인한다.
3. 모델 파인 튜닝: 구축된 특정 도메인의 데이터셋을 활용해 범용 임베딩 모델을 특정 도메인(법률, 의료 등) 데이터에 맞춰 재학습(Fine-tuning)한다.
4. 자동 평가(Auto-Evaluation): 후보 모델과 현재 운영 모델의 성능을 비교 평가하고, 개선이 검증된 경우에만 배포 가능한 상태로 등록한다.

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

#### Metadata & Search DB (통합 저장소)

1. 통합 데이터 저장 및 정합성 보장: 사용자 정보, 영상 메타데이터, 파이프라인 상태, 텍스트 청크, 임베딩 벡터 등 관계형 데이터와 검색용 데이터를 하나의 다목적 DB(Multi-model DB)에 저장하여 단일 트랜잭션으로 관리한다. 삭제 및 복구 시 유령 데이터 발생을 예방한다.

2. 하이브리드 검색 엔진 내장: DB 내부에 텍스트 역색인(FTS) 확장과 고차원 벡터 탐색(ANN) 확장을 구성하여, 단일 쿼리로 사용자 권한 사전 필터링(video_id), 단어 일치 검색, 의미 기반 벡터 검색을 동시에 수행한다.

#### Cache

1. 반복 조회 최적화: 자주 조회되는 검색 결과와 처리 상태를 캐싱하여 저장소 부하를 줄이고, 검색 응답 시간 목표(예: 5초 이내) 달성에 기여한다.

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
* Client → API Server: 영상 메타데이터 (제목, 카테고리, 입력 방식), status=PENDING 생성
* (Local File) API Server → Client: Presigned URL + 영상 고유 ID
* (Local File) Client → Object Storage: 영상 원본 파일
* (Local File) Client → API Server: 업로드 완료 신호 → status=UPLOADED 갱신
* (External URL) API Server → Message Broker: 다운로드 작업 요청
* (External URL) Media Processor → Object Storage: 다운로드한 영상 원본 파일 → status=UPLOADED 갱신

### 2. Media Preprocessing
* Message Broker → Media Processor: 전처리 작업 요청 → status=PROCESSING 갱신
* Object Storage → Media Processor: 영상 원본 파일
* Media Processor → Object Storage: 추출된 오디오 파일, 키프레임 이미지
* Media Processor → Metadata DB: 오디오/키프레임 경로 및 메타데이터
* Media Processor → Message Broker: 전처리 완료 이벤트 발행

### 3. AI Analysis & Indexing
* Message Broker → AI Pipeline Worker: AI 처리 작업 요청
* Object Storage → AI Pipeline Worker: 오디오 파일, 키프레임 이미지
* AI Pipeline Worker → AI Model Gateway: 오디오 파일 전송 (STT 추론 표준 요청)
* AI Model Gateway → External AI Adapters: 외부 STT API(OpenAI 등) 호출 및 예외 처리
* External AI Adapters → AI Model Gateway: 원시 텍스트 응답 수신
* AI Model Gateway → AI Pipeline Worker: 스크립트 + 타임스탬프 (내부 표준 포맷으로 반환)
* AI Pipeline Worker → AI Model Gateway: 텍스트 청크 + 키프레임 전송 (임베딩 추론 표준 요청)
* AI Model Gateway → Managed Embedding Endpoint: 자체 호스팅 모델에 벡터화 요청
* Managed Embedding Endpoint → AI Model Gateway: 임베딩 벡터 반환
* AI Model Gateway → AI Pipeline Worker: 임베딩 벡터 (내부 표준 포맷으로 반환)
* AI Pipeline Worker → Metadata & Search DB: 스크립트, 청크 텍스트, 임베딩 벡터, 타임스탬프를 단일 트랜잭션으로 적재 → status=READY 갱신 (실패 시 FAILED)

### 4. Search & RAG Serving
* Client → Edge Gateway → Search Service: 자연어 질의
* Search Service → Cache: 캐시 조회
* (Hit) Cache → Search Service: 캐시된 답변 + 타임스탬프
* (Miss) Search Service → AI Model Gateway: 질의 임베딩 변환 요청
* (Miss) AI Model Gateway → Search Service: 질의 임베딩 벡터
* (Miss) Search Service → Metadata & Search DB: 질의 벡터 기반 하이브리드 검색 쿼리 단일 실행
* Metadata & Search DB → Search Service: 필터링 및 병합(RRF)이 완료된 Top-K 청크 목록
* Search Service → AI Model Gateway: Top-K 청크 + 질의 (LLM 요청)
* AI Model Gateway → Search Service: 생성된 답변
* Search Service → Client: 답변 + 근거 타임스탬프

### 5. Feedback & Model Improvement
* Client → Edge Gateway → Core API Server: 명시적 피드백 (좋아요/싫어요)
* Core API Server → Metadata & Search DB: 피드백 + query_text + topk_chunk_ids + cited_chunk_ids 저장
* Pipeline Controller → Message Broker: 모델 재학습 작업 큐 발행
* Message Broker → Model Training Worker: 재학습 작업 소비
* Metadata & Search DB → Model Training Worker: like 피드백 로그 로드
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
1. Core API Server가 메타데이터를 Metadata & Search DB에 저장하고 영상 고유 ID 생성
2. Core API Server가 Object Storage에 업로드 가능한 Presigned URL 발급
3. Client가 Presigned URL을 통해 Object Storage에 직접 영상 파일 업로드
4. Client가 Edge Gateway를 거쳐 Core API Server에 업로드 완료 신호 전송
5. Core API Server가 status를 UPLOADED로 갱신 후 Message Broker에 전처리 작업 큐 발행

**출력**
- Presigned URL + 영상 고유 ID → Client 반환
- 전처리 작업 메시지 → Message Broker 발행

**저장 위치**
| 데이터 | 저장소 |
|--------|--------|
| 영상 원본 파일 | Object Storage |
| 영상 메타데이터 (제목, 카테고리, 업로더, status=UPLOADED) | Metadata & Search DB |

## 2.2 Video Ingest (External URL)

**입력**
- 주체: Client
- 데이터: 외부 영상 URL, 메타데이터 (제목, 카테고리)

**처리**
1. Core API Server가 메타데이터를 Metadata & Search DB에 저장하고 영상 고유 ID 생성
2. Core API Server가 Message Broker에 다운로드 작업 큐 발행
3. Media Processor가 외부 URL에서 영상을 다운로드하여 Object Storage에 저장
4. Media Processor가 status를 UPLOADED로 갱신 후 Message Broker에 전처리 작업 큐 발행

**출력**
- 전처리 작업 메시지 → Message Broker 발행

**저장 위치**
| 데이터 | 저장소 |
|--------|--------|
| 영상 원본 파일 | Object Storage |
| 영상 메타데이터 (제목, 카테고리, 업로더, source_url, status=UPLOADED) | Metadata & Search DB |

## 2.3 Media Preprocessing

**입력**
- 주체: Media Processor (Message Broker로부터 전처리 작업 소비)
- 데이터: 영상 원본 파일 (Object Storage)

**처리**
1. Object Storage에서 영상 원본 파일 로드
2. 영상에서 오디오 추출
3. 영상에서 키프레임 추출 (화면 전환 감지 기준)

**출력**
- 오디오 파일 → Object Storage 저장
- 키프레임 이미지 → Object Storage 저장
- 오디오/키프레임 경로 및 메타데이터 → Metadata DB 저장
- 전처리 완료 이벤트 → Message Broker 발행

**저장 위치**
| 데이터 | 저장소 |
|--------|--------|
| 오디오 파일 | Object Storage |
| 키프레임 이미지 | Object Storage |
| 오디오/키프레임 경로, status=PROCESSING | Metadata & Search DB |


## 2.4 AI Analysis & Indexing

**입력**
- 주체: AI Pipeline Worker (Message Broker로부터 AI 처리 작업 소비)
- 데이터: 오디오 파일, 키프레임 이미지 (Object Storage)

**처리**
1. 오디오 파일을 처리 단위로 분할하여 AI Model Gateway에 전달 → Gateway가 External AI Adapters를 통해 외부 STT API 호출 → 반환된 결과를 Gateway가 표준화하여 스크립트+타임스탬프로 Worker에 전달
2. Worker가 전체 스크립트를 문맥 단위로 시맨틱 청킹
3. 텍스트 청크 타임스탬프 구간에 해당하는 키프레임 매핑
4. 텍스트 청크 + 키프레임을 AI Model Gateway에 전달 → Gateway가 Managed Embedding Endpoint(자체 배포 모델)로 임베딩 요청 → 벡터화된 결과 반환
5. 스크립트, 청크 텍스트, 임베딩 벡터를 Metadata & Search DB에 단일 트랜잭션으로 적재

**출력**
- 전체 스크립트 + 타임스탬프 → Metadata & Search DB 단일 트랜잭션 적재
- 청크 텍스트 + 임베딩 벡터 + 타임스탬프 + 키프레임 참조 → Metadata & Search DB 단일 트랜잭션 적재

**저장 위치**
| 데이터 | 저장소 |
|--------|--------|
| 전체 스크립트, 타임스탬프 | Metadata & Search DB |
| 청크 텍스트, 임베딩 벡터, 타임스탬프, 키프레임 참조, chunk_id | Metadata & Search DB |
| status=READY (실패 시 FAILED + failed_stage) | Metadata & Search DB |

## 2.5 Search & RAG Serving

**입력**
- 주체: Client
- 데이터: 자연어 질의 텍스트

**처리**
1. Search Service가 Cache 조회 → Hit 시 즉시 반환
2. (Miss 시) 질의 텍스트를 AI Model Gateway를 통해 임베딩 벡터로 변환
3. Search Service가 Metadata & Search DB에 단일 하이브리드 쿼리(사용자 필터 + FTS + Vector) 실행
4. Metadata & Search DB 내부 연산을 거쳐 RRF 병합된 Top-K 청크 반환
5. Search Service가 Top-K 청크 + 질의를 AI Model Gateway에 전달 → LLM이 최종 답변 생성

**출력**
- 생성된 답변 + 근거 타임스탬프 → Client 반환
- 답변 + 타임스탬프 → Cache 저장

**저장 위치**
| 데이터 | 저장소 |
|--------|--------|
| 질의 결과 캐시 (답변 + 타임스탬프) | Cache |

## 2.6 처리 실패 (FAILED)

**발생 시점**
- Media Preprocessing 또는 AI Analysis & Indexing 단계에서 오류 발생 시

**처리**
1. 실패한 단계의 Worker가 Metadata & Search DB에 status=FAILED 및 failed_stage 기록
2. 해당 시점까지 생성된 중간 산출물(오디오, 키프레임, 청크 등)은 삭제하지 않고 보존
3. 이후 재처리 요청 시 failed_stage 기준으로 해당 단계부터 재시작

**저장 위치**
| 데이터 | 저장소 |
|--------|--------|
| status=FAILED, failed_stage, error_message | Metadata & Search DB |

## 2.7 Feedback 수집

**발생 시점**
- 사용자가 검색 결과에 대해 좋아요/싫어요를 누를 때

**처리**
1. Client가 Edge Gateway를 거쳐 Core API Server에 피드백 전송
2. Core API Server가 해당 시점의 topk_chunk_ids, cited_chunk_ids를 함께 수집
3. Core API Server가 Metadata & Search DB에 피드백 및 컨텍스트 로그 저장

**저장 위치**
| 데이터 | 저장소 |
|--------|--------|
| user_id, video_id, query_text, rating, topk_chunk_ids, cited_chunk_ids, created_at | Metadata & Search DB |

---

## 2.8 모델 재학습 및 배포

**발생 시점**
- Pipeline Controller가 재학습 작업을 트리거할 때

**처리**
1. Pipeline Controller가 Message Broker에 재학습 작업 큐 발행
2. Model Training Worker가 Metadata & Search DB에서 like 피드백 로그 로드
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
- failed_stage: 실패 시 어느 단계에서 실패했는지
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
시맨틱 청킹 결과물로 하이브리드 검색의 기본 단위 (통합 DB 릴레이션)

- id: 청크 고유 ID
- video_id: 연관된 영상 ID (Video 참조)
- start_ms: 구간 시작 시각 (밀리초)
- end_ms: 구간 종료 시각 (밀리초)
- text: 청크 텍스트 (전문 검색 인덱싱용)
- embedding_vector: 임베딩 벡터 배열 (ANN 벡터 탐색용)
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

