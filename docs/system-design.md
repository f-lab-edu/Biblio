# Architecture Overview

## 1.1 주요 컴포넌트

### 인터페이스 및 진입점 (Interface Layer)

**목적:** 사용자 및 관리자의 요청을 받아 적절한 로직으로 라우팅하고, 시스템 진입 구간에서 기본적인 보안/가용성을 확보

#### Client (User Web UI)

1. 업로드 및 검색 UI 제공: 로컬 파일 업로드 또는 외부 URL 입력, 자연어 질의 입력, 검색 결과(근거 구간/타임스탬프) 표시
2. 처리상태 조회 및 목록 관리: 업로드한 영상 목록 조회, 제목 수정/삭제, 처리 상태(업로드 완료/처리 중/완료) 조회
3. 사용자 피드백 입력: 좋아요/싫어요 등 명시적 피드백을 입력하고, 클릭/조회 등 묵시적 피드백이 수집될 수 있도록 이벤트를 발생

#### Edge Gateway (API Gateway / Load Balancer)

1. 요청 수신 및 라우팅: 외부 요청을 수신하고 API 서버로 라우팅한다.
2. 트래픽 분산 및 가용성: 동시 업로드 등 트래픽이 증가할 때 요청을 분산시켜 서비스 중단을 방지하고 가용성 목표(예: 99%)를 만족하도록 돕는다.

#### API Server

1. 사용자 요청 처리: 영상 업로드, 검색 질의, 처리 상태 조회, 영상 목록 관리 등 클라이언트 요청을 처리한다.
2. presigned URL 발급: 권한 확인 후 업로드 가능한 URL과 영상 고유 id 를 발급
3. 메타데이터 및 초기 상태 저장: 영상에 대한 메타데이터(업로더, 영상이름,카테고리)와 초기 상태를 db에 저장
4. 업로드 완료 확인/처리 트리거: 클라이언트로 부터 업로드가 끝났다는 신호를 받아 파이프라인 작업을 큐에 넣고, 상태를 업데이트
5. 인증 및 인가: 사용자별 영상 접근 권한을 제어하여, 본인이 업로드한 영상과 결과에만 접근하도록 보안을 유지한다.
6. 피드백 수집(Feedback Ingestion): 검색 결과에 대한 좋아요/싫어요(명시적 피드백) 및 클릭 로그(묵시적 피드백)를 수집하여 저장소에 적재한다.
7. 비동기 작업 요청: 영상 처리처럼 시간이 오래 걸리는 작업은 직접 처리하지 않고 메시지 브로커로 전달하여, 즉시 “요청 접수(Accepted)” 응답을 반환한다.
8. 하이브리드 검색 조정(Search Orchestrator): 사용자 질의에 대해 키워드 기반의 FTS(Full-Text Search)와 의미 기반의 시맨틱(Vector) 검색을 병렬로 수행. 각 검색 결과를 병합하고 중복을 제거한 뒤, 재순위화(Re-ranking) 과정을 거쳐 타임스탬프 메타데이터가 포함된 상위 K개(Top-K)의 근거 청크를 추출.
   이렇게 구성된 컨텍스트를 LLM(Answer Generator)에 전달하여 최종 답변을 생성하며, 결과 반환 시에는 근거 청크에 매핑된 타임스탬프와 레퍼런스를 함께 제공한다.

---

### 메시징 및 비동기 중계 (Messaging Layer)

**목적:** 무거운 영상 처리/학습 작업을 API 서버에서 분리하여 시스템 반응성을 높이고, 장애 격리를 달성

#### Message Broker

1. 작업 대기열 관리: API 서버로부터 전달받은 '영상 처리 요청'을 큐(Queue)에 적재하여, Worker가 처리 가능한 시점에 작업을 가져가도록 한다.
2. 워크로드 격리: 모델 학습(Training) 큐를 별도로 운영하여, 학습 부하가 업로드/검색 처리에 영향을 주지 않도록 분리한다.

---

### 워커 (Worker Layer)

**목적:** 실질적인 연산(Compute)을 담당하며, 파이프라인 처리량/확장성을 확보

#### Media Processor

1. URL 영상  저장: 외부 URL 입력의 경우 해당 url속 영상을 다운로드 하여 스토리지에 저장한다
2. 오디오 추출: 저장된 원본 영상에서 음성 파일을 추출하여 스토리지에 저장하고, AI 모델이 음성→텍스트 변환을 수행할 수 있도록 준비한다.
3. 키프레임 추출: 영상의 화면 전환(슬라이드 변경 등)이 발생하는 시점을 감지하여 중복된 이미지를 제외하고, 시각적 정보량이 변하는 구간의 대표 이미지와 그 타임스탬프만 선별적으로 추출하여 저장한다

#### AI Pipeline Worker

1. STT 추론 요청 및 스크립트 적재: Media Processor가 스토리지에 저장한 오디오를 로드하여 AI Model Gateway에 STT 추론을 요청하고, 반환된 스크립트(타임스탬프 포함)를 DB에 저장한다.
2. 시맨틱 청킹 (Semantic Chunking): 변환된 텍스트(Script)를 받아 문맥 단위로 분할하여 검색 가능한 청크(Chunk)를 생성한다.
3. 멀티모달 매핑 (Alignment): 생성된 텍스트 청크(예: 00:15 ~ 00:45)에 해당하는 구간에서 가장 적절한 키프레임을 Media Processor가 추출해둔 이미지 중에서 찾아 매핑
4. 임베딩 추론 요청 및 검색 색인 구축: Semantic Chunking/Alignment 결과(텍스트 청크 + 키프레임 참조)를 입력으로 AI Model Gateway에 임베딩 추론을 요청하고, 반환된 벡터와 메타데이터를 Vector Store/Index에 적재한다.
5. 처리 안정성 보장(부분 실패 대응): 각 단계는 재실행 가능하도록(같은 입력이면 같은 결과가 나오도록) 설계하고, 중간 산출물/부분 저장 데이터 정리를 통해 데이터 정합성(유령 데이터 방지)을 유지한다.

#### Model Training Worker

1. 데이터셋 전처리: 사용자 피드백 및 관리자가 확정한 학습용 데이터셋을 로드하여 학습 가능한 포맷으로 변환한다.
2. 모델 성능 개선: 피드백 기반 데이터셋을 활용해 임베딩 모델을 개선하고, 필요 시 기존 벡터 데이터를 재색인한다.
3. 모델 파인 튜닝: 구축된 특정 도메인의 데이터셋을 활용해 범용 임베딩 모델을 특정 도메인(법률, 의료 등) 데이터에 맞춰 재학습(Fine-tuning)한다.
4. 자동 평가(Auto-Evaluation): 후보 모델과 현재 운영 모델의 성능을 비교 평가하고, 개선이 검증된 경우에만 배포 가능한 상태로 등록한다.

---

### AI 모델 서빙 (AI Model Serving Layer)

**목적:** 비즈니스 로직(Worker/API)과 AI 모델 실행 환경(Infra)을 분리하여 확장성과 유연성을 확보한다.

#### AI Model Gateway (Inference Layer)

1. 추론 인터페이스 단일화: Worker나 API Server가 로컬 모델(Local GPU)을 쓰는지 외부 API(Vertex AI, OpenAI)를 쓰는지 알 필요 없도록 통일된 API(예: transcribe, embed, generate_answer)를 제공한다.
2. 리소스 및 비용 최적화: 로컬 모델 서빙 시 배치(Batch) 처리를 통해 GPU 효율을 높이거나, 외부 API 호출 시 속도 제한(Rate Limiting) 및 키 관리를 수행한다.
3. LLM 연동 (RAG Generation): API Server로부터 전달받은 프롬프트(질문+문맥)를 LLM에 전달하고, 생성된 자연어 답변을 반환한다.

---

### 데이터 저장소 (Storage Layer)

**목적:** 데이터의 특성에 따라 저장소를 분리하여 성능/내구성/검색 효율을 확보

#### Object Storage

1. 대용량 파일 저장: 원본 영상, 추출된 오디오, 키프레임 이미지 등 대용량 파일을 저장한다.

#### Metadata DB (RDB)

1. 메타데이터 저장: 사용자 정보, 영상 메타데이터(제목/길이/업로드 시간/카테고리), 처리 상태, 스크립트/청크 텍스트, 피드백 로그, 학습 데이터셋 이력, 모델 평가 지표 등을 저장한다.

#### Text Search Index (Keyword Search)

1. 키워드 검색용 인덱스: 스크립트/청크 텍스트에 대해 단어 일치 기반 검색이 가능하도록 인덱스를 구축한다.
   ※ 구현은 RDB의 전문 검색 기능을 활용하거나 별도 검색 인덱스로 분리하는 등 환경에 따라 선택 가능하다.

#### Vector Store (Semantic Search)

1. 임베딩 벡터 저장 및 유사도 검색: AI Worker가 생성한 임베딩 벡터를 저장하고, 자연어 질문에 대한 의미 기반 검색을 수행한다.

#### Cache

1. 반복 조회 최적화: 자주 조회되는 검색 결과와 처리 상태를 캐싱하여 저장소 부하를 줄이고, 검색 응답 시간 목표(예: 5초 이내) 달성에 기여한다.

#### Model Registry / Artifact Store

1. 모델 버전 및 산출물 관리: STT/임베딩 모델의 버전, 평가 지표, 배포 승인 상태, 모델 파일(아티팩트) 등을 관리하여 교체/롤백이 가능하도록 한다.

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

* 오케스트레이션: 파이프라인 상태를 기준으로 단계 실행 순서, 재시도/선별적 재처리, 강제 삭제 같은 운영 명령을 내리고 워크플로우를 관리한다.
* 모델 교체 및 재색인 트리거: 임베딩/STT 모델 버전 변경 시, 기존 데이터를 새로운 모델로 다시 벡터화(재색인)하는 작업을 트리거한다.
* 배포/롤백 제어: 평가를 통과한 모델을 배포하고, 문제 발생 시 이전 버전으로 즉시 롤백할 수 있도록 제어 로직을 제공한다.

---

## 1.2 컴포넌트 간 데이터 흐름

비디오 업로드부터 검색/응답까지의 핵심 데이터 이동 경로를 요약
메타데이터 DB는 전체 파이프라인의 상태(Status)를 관리한다.

**Status 전이:** `PENDING → UPLOADED → PROCESSING → READY (또는 FAILED)`

### 1. 비디오 수집 및 원본 저장 (Video Ingest)

* Client → API Server: 업로드 요청 (메타데이터 생성, status=PENDING).
* 입력 방식에 따른 분기:

  * (Local File) API Server가 Presigned URL 발급 → Client가 Object Storage에 직접 업로드.
  * (External URL) API Server가 다운로드 요청 발행 → Media Processor가 원본 다운로드 후 Object Storage 저장.
* Completion: 원본 저장 완료 시 status=UPLOADED로 갱신하고, Message Broker에 수집 완료 이벤트를 발행하여 다음 단계를 트리거한다.

### 2. 미디어 전처리 (Media Preprocessing)

* Message Broker → Media Processor: 수집 완료 이벤트 소비(Consume) 및 작업 시작.
* Media Processor → Object Storage: 원본 비디오에서 오디오(Audio)와 키프레임(Keyframe) 추출 후 저장.
* Completion: 산출물 메타데이터 저장 후 status=PROCESSING으로 전환, Message Broker에 전처리 완료 이벤트를 발행하여 AI 파이프라인으로 핸드오프.

### 3. AI 분석 및 인덱싱 (AI Analysis & Indexing)

* Message Broker → AI Pipeline Worker: 전처리 완료 이벤트 소비 및 AI 처리 작업 시작.
* AI Pipeline Worker → AI Model Gateway: STT(Speech-to-Text) 변환 및 임베딩(Embedding) 생성 요청.
* AI Pipeline Worker → Storage & Indices: 데이터 적재.

  * Metadata DB: 스크립트, 타임스탬프, 청크 정보 저장.
  * Vector Store & Text Search Index: 검색을 위한 벡터 및 키워드 인덱스 적재.
* Completion: 모든 인덱싱 완료 시 status=READY로 최종 갱신 (실패 시 FAILED 및 stage에 원인 기록).

### 4. 검색 및 RAG 응답 (Search & RAG Serving)

* Client → API Server: 사용자 질의(Query) 요청.
* API Server → Cache: 캐시 히트 시 즉시 반환.
* API Server → Vector Store & Text Search Index: (Miss 시) 벡터 유사도와 키워드 검색을 병렬 수행하여 후보군 확보.
* API Server → AI Model Gateway(LLM): 검색된 **증거 데이터(Top-K Evidence)**와 사용자 질문을 조합하여 LLM에 전달.
* API Server → Client: LLM이 생성한 답변 및 근거가 되는 비디오 구간 정보 반환.

---


