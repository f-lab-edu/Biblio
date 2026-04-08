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

**User 기능**

1. 사용자 요청 처리: 영상 업로드, 처리 상태 조회, 영상 목록 관리 등 클라이언트 요청을 처리한다.
2. presigned URL 발급: 권한 확인 후 업로드 가능한 URL과 영상 고유 id 를 발급
3. 메타데이터 및 초기 상태 저장: 영상에 대한 메타데이터(업로더, 영상이름,카테고리)와 초기 상태를 db에 저장
4. 업로드 완료 확인/처리 트리거: 클라이언트로 부터 업로드가 끝났다는 신호를 받아 파이프라인 작업을 큐에 넣고, 상태를 업데이트
5. 인증 및 인가(AuthN/AuthZ): 전달받은 Access Token(JWT)의 서명과 만료를 내부 미들웨어에서 직접 검증한다. 이후 claim에서 추출한 requester_user_id를 기준으로 영상 리소스(video_id)의 소유권을 확인하여, 본인이 업로드한 영상과 데이터에만 접근하도록 제어한다.
6. 피드백 수집(Feedback Ingestion): 검색 결과에 대한 좋아요/싫어요를 영상 단위가 아닌 검색 응답 단위로 수집한다. Core API Server는 사용자 권한과 피드백 요청의 유효성을 검증한 뒤, 검증된 피드백 이벤트를 Feedback Ingestion Pipeline으로 전달한다.
7. 비동기 작업 요청: 영상 처리처럼 시간이 오래 걸리는 작업은 직접 처리하지 않고 메시지 브로커로 전달하여, 즉시 “요청 접수(Accepted)” 응답을 반환한다.
8. 영상 삭제 요청 처리: 사용자의 삭제 요청을 수신하여 Video.status를 DELETING으로 전이하고 DELETE_REQUEST를 메시지 브로커에 발행한다. 실제 연쇄 삭제는 Pipeline Worker에 위임한다.
9. 파이프라인 실패 재처리: 사용자의 재시도 요청을 수신하여 Video.status를 PENDING으로 초기화하고 PREPROCESS_REQUEST를 재발행하여 Worker가 실패 지점부터 재개할 수 있도록 한다.

**Admin 기능**

Admin 기능은 JWT claim의 role을 기준으로 운영자 권한을 별도 검증하며, 소유권(user_id) 기반 제한 없이 모든 리소스에 접근한다.

10. 전체 파이프라인 상태 조회: 소유권 제한 없이 임의 video_id의 처리 현황 및 실패 상세를 조회한다.
11. 강제 재처리: 임의 video_id에 대해 재처리가 가능하도록 처리 상태를 조정하고, 후속 처리 파이프라인을 다시 시작한다.
12. 강제 삭제: 임의 video_id에 대해 삭제 절차를 시작하고, 연쇄 정리 작업을 수행하도록 요청한다.
13. ML 파이프라인 상태 조회 및 재트리거: MLPipelineRun의 진행 상태 및 실패 현황을 조회하고, 장애 시 수동 재트리거 또는 롤백 액션을 요청한다.


#### Search Service

1. 검색 전담 처리: 자연어 질의를 수신하며, 요청자 소유 검색 범위의 영상이 1개 이상 존재하고 그 전체가 `READY`일 때만 하이브리드 검색 파이프라인(임베딩 변환 및 DB/Vector 조회)을 시작한다. 영상이 0개면 `409 NO_VIDEOS_UPLOADED`, 1개 이상이지만 하나라도 미준비 상태면 `409 SEARCH_NOT_READY`를 반환한다.
2. 하이브리드 검색 오케스트레이션 (Search Orchestrator):
   - 내부 추상화 인터페이스를 통해 **Managed Embedding Endpoint를 직접 호출**하여 질의 텍스트를 벡터로 변환한다.
   - 키워드 후보는 Metadata DB(FTS)에서, 벡터 후보는 Vector Store(ANN)에서 각각 생성한다.
   - 두 후보를 RRF(Reciprocal Rank Fusion) 등으로 병합하여 최종 Top-K를 만든다.
   - 최종 응답 전에 Metadata DB(SOT)에서 최종 서빙 검증을 수행하여 (권한/존재 여부(삭제=hard delete)/READY 상태) 기준을 통과한 Chunk만 반환한다.
3. LLM 컨텍스트 주입: 추출된 청크들을 조립한 프롬프트를 Search Service 내부 LLM 연동 구현체에 전달하여 최종 답변을 생성하며, 피드백 귀속용 `req_id`, 타임스탬프 레퍼런스 `ref`, 실제 인용 여부 `used`가 포함된 `chunks` 배열을 함께 반환한다.
4. 인증 및 테넌시 강제(AuthN/AuthZ): 전달받은 Access Token(JWT)을 직접 검증하여 requester_user_id를 추출한다. 이를 기준으로 검색 대상 범위를 제한하고, Metadata DB(FTS) 및 Vector Store(ANN) 조회 단계 모두에서 사용자 테넌트 필터를 반드시 강제한다.


---

### 비동기 파이프라인 서브시스템 (Async Pipeline Subsystem)

**목적:** API 서버와 무거운 데이터 처리/학습 작업을 비동기적으로 분리(Decoupling)하여 시스템의 응답성을 유지하고, 메시지 큐 기반의 유연한 확장을 통해 대용량 미디어 및 AI 연산을 안정적으로 처리한다.

#### Message Broker

1. 작업 대기열 관리: API 서버로부터 전달받은 '영상 처리 요청'을 큐(Queue)에 적재하여, Worker가 처리 가능한 시점에 작업을 가져가도록 한다.
2. 워크로드 격리: 모델 학습(Training) 큐를 별도로 운영하여, 학습 부하가 업로드/검색 처리에 영향을 주지 않도록 분리한다.
3. 메시지 계약(Message Contract): 파이프라인 메시지는 공통 Envelope + 메시지별 Payload로 구성하며, 최소 필드/스키마는 Data Model의 “Message Contract(3.9~)” 정의를 따른다.

#### Feedback Ingestion Pipeline

1. 피드백 이벤트 수집: Core API Server가 전달한 검증된 피드백 이벤트를 수신한다.
2. 로그 적재: 피드백 이벤트를 수정 없이 누적 저장하는 원본 로그 형태로 Object Storage에 적재한다.
3. 내결함성: 일시적 전송 장애 시 버퍼링 및 재전송을 통해 이벤트 손실을 최소화한다.

#### Media & AI Pipeline Worker (통합 워커)

1. 단일 파이프라인 실행: 큐에서 PROCESS_REQUEST 수신 시, 하나의 프로세스 내에서 `다운로드 → 추출 → STT → 청킹 → 임베딩 → DB 적재`를 순차적으로 논스톱 처리하여 네트워크 I/O 지연을 극소화한다.
2. 멱등성 보장 다운로드 및 전처리: 외부 URL 인입 시 대상 파일이 스토리지에 존재하는지 확인(멱등성 체크) 후, 없으면 다운로드하여 상태를 UPLOADED로 갱신한다. 이후 로컬 환경에서 오디오와 키프레임을 추출한다.
3. 데이터 지역성 기반 AI 서비스 직접 연동: 추출된 오디오 데이터를 스토리지 다운로드 대기 없이 로컬 환경에서 Pipeline Worker 내부 STT 연동 구현을 통해 외부 음성 인식 서비스로 직접 전송하여 텍스트 스크립트를 반환받는다. (추출된 원본 오디오 및 키프레임은 장애 복구 및 서비스 서빙을 위해 Object Storage에 비동기 백업 적재한다.)
4. 시맨틱 청킹 및 벡터화: 변환된 스크립트를 문맥 단위로 분할(Chunking)하고 타임스탬프에 맞는 키프레임을 매핑한다. 청킹 및 Vision enrichment 단계는 추출 직후 확보한 로컬 키프레임 메타데이터를 즉시 사용하며, Object Storage 백업 및 Asset 레코드 기록은 병렬로 진행하되 최종 Chunk 적재 전에는 완료되어 `keyframe_asset_id`를 확정해야 한다. 이후 텍스트 청크를 **Managed Embedding Endpoint**로 직접 전송하여 임베딩 벡터를 반환받는다.
5. 검색 색인 구축 및 완료: 전체 대본/청크 메타데이터는 Metadata DB(SOT)에 트랜잭션 적재하고, 임베딩 벡터는 Vector Store(ANN)에 Upsert 한다. 반영 완료 시 Video.status를 READY로 갱신한다.
6. 부분 실패 및 장애 복구: 작업 실패 시 기존 산출물(스토리지 백업본)을 보존하며 DB에 failed_stage를 기록한다. 재처리 요청 시 완료된 무거운 작업(영상 다운로드 등)은 건너뛰고(Skip), failed_stage와 보존 산출물을 함께 참조하여 안전한 재개 지점을 판단할 수 있도록(Resume) 복구력을 보장한다.
7. 영상 삭제 연쇄 처리: DELETE_REQUEST 수신 시, 또는 파이프라인 처리 중 각 단계 진입 전 Video.status=DELETING을 감지한 경우 현재 단계에서 중단하고 연쇄 삭제를 수행한다. Metadata DB의 관련 레코드(VectorIndexEntry, Chunk, TranscriptSegment, Asset, Video)를 트랜잭션으로 삭제하고, Object Storage 파일(원본 영상, 오디오, 키프레임)은 메인 서비스와 분리하여 비동기로 정리한다.

#### ML Lifecycle Worker

1. 데이터셋 전처리: 정기 배치 스케줄의 실행 책임은 ML Lifecycle Worker가 가진다. Worker는 정기 배치에 따라 신규 피드백을 학습 가능한 형태의 데이터셋으로 변환하여 Object Storage에 저장한다. 배치 실행은 중복 수행하지 않으며, 전처리 완료 후 학습 파이프라인을 자동 트리거한다.
2. 모델 학습: 최신 데이터셋을 입력으로 임베딩 모델 개선 학습을 자동 수행하고 후보 모델을 Model Artifact Files에 저장한다.
3. 모델 평가 및 결과 저장: 후보 모델과 기준 모델의 검색 성능을 별도 평가용 데이터셋으로 자동 비교 평가한다. 집계 결과는 Metadata DB에, 질의별 상세 결과는 아티팩트로 저장한다. 평가 결과는 품질 미달(FAIL)과 시스템 오류(ERROR)를 구분하여 기록한다.
4. 재색인 실행: 평가 PASS 시 후보 모델 전용 인덱스를 별도로 구축한다. 재색인 중에도 사용자 검색은 기존 모델과 인덱스로 계속 제공한다. 후보 인덱스는 이번 실행의 후보 모델 기준으로 구축하며, 전환 기준 시각까지의 데이터 반영과 후보 모델 readiness가 모두 확인된 뒤에만 서빙을 전환한다
5. 실행 제어 및 실패 처리: 동시에 활성 상태인 MLPipelineRun은 하나만 유지한다. 실행 중 새 데이터셋이 준비되면 FIFO로 모두 쌓지 않고, 최신 데이터셋 기준의 다음 실행만 남긴다. 각 단계의 진행 상태와 실패 정보는 MLPipelineRun에 계속 기록하며, 신규 데이터셋으로 대체된 실행은 SUPERSEDED로 표시한다.
6. 내부 책임 분리 원칙: ML Lifecycle Worker는 단일 배포 단위로 유지하되, 내부 구현은 단계별 책임이 섞이지 않도록 1모듈 1책임 원칙으로 분리한다. 실행 제어, 학습/평가, 재색인, 서빙 전환 책임은 서로 독립적으로 변경·재실행 가능해야 하며, 구체적인 모듈 구조와 상호작용은 후속 Spec에서 정의한다.

---

### AI 추론 서브시스템 (AI Inference Subsystem)

**목적:** 무거운 AI 연산 자원을 워커와 물리적으로 분리하여(Dedicated) 자원 효율성과 독립적인 오토스케일링을 확보한다.

#### Managed Embedding Endpoint (자체 호스팅 모델)

1. 임베딩 추론 전담: Worker 및 Search Service로부터 API 요청을 직접 수신하여 텍스트를 벡터로 변환하는 연산에만 집중한다.
2. 모델 관리 및 서빙 기준: 런타임 서빙 모델은 Metadata DB의 릴리스 레코드를 SOT로 결정한다. 최초 기동 시 릴리스 레코드가 없으면 배포 설정(환경변수)의 기본값으로 폴백하고, 기동 후 릴리스 레코드를 초기화한다. Endpoint는 현재 활성 서빙 모델과 재색인/검증에 사용되는 후보 모델을 동시에 유지할 수 있으며, 어떤 모델을 사용할지는 요청 목적과 릴리스 레코드에 따라 결정한다.
3. 서빙 준비성(readiness): 활성 모델 아티팩트 로드가 성공해야만 요청을 받으며, 후보 모델은 ML Lifecycle Worker의 재색인/검증 트래픽에만 노출된다. 후보 모델이 실제로 로드되고 readiness가 통과한 뒤에만 ModelRelease를 갱신한다.

---

### 데이터 저장소 (Data & Storage Subsystem)

**목적:** 데이터의 특성에 따라 저장소를 분리하여 성능/내구성/검색 효율을 확보

#### Object Storage

1. 대용량 파일 저장: 원본 영상, 추출된 오디오, 키프레임 이미지 등 대용량 파일을 저장한다.
2. 운영 산출물 저장: 평가 상세 아티팩트 같은 운영 산출물을 저장한다.
3. 원본 이벤트 로그 저장: 검색 응답 단위 피드백 이벤트를 수정 없이 누적 저장하는 원본 로그 형태로 저장한다.
4. 평가용 데이터셋 저장: 모델 평가에 사용하는 입력/정답 기준 데이터셋을 별도 버전 산출물로 저장한다. 사용자 업로드 자산과 ML 운영 산출물은 논리적으로 분리하여 관리한다

#### Metadata DB (Source of Truth; RDB)

1. 정합성 보장(SOT): 사용자, 영상 메타데이터, 상태(Status), Transcript/Chunk(텍스트/타임스탬프/참조)를 ACID 트랜잭션으로 저장한다.
2. 키워드 검색(FTS): Chunk의 enriched_text(없을 경우 text)에 대한 FTS 인덱스를 운영하여 키워드 후보를 생성한다. (초기 구성: RDB 내 FTS)
3. 최종 서빙 검증(SOT Validation): 검색 결과로 반환되기 전, 권한/존재 여부(삭제=hard delete)/상태(READY) 기준으로 노출 가능한 Chunk만 최종 확정하는 기준 저장소로 동작한다.
4. 운영 메타데이터 저장: 피드백 기반 모델 평가 결과와 운영 상태 추적에 필요한 메타데이터를 저장한다.
5. 모델 릴리스 상태 관리(SOT): 현재 서빙 중인 활성 모델 버전, 후보 모델 버전, 롤백 대상을 릴리스 레코드로 관리한다. 모델 전환과 롤백의 기준은 이 레코드 갱신을 따른다.

#### Vector Store (ANN Index; Derived Projection)

1. 벡터 저장(파생 인덱스): Chunk 단위 임베딩 벡터를 저장하며, 검색 시 테넌시 필터를 적용할 수 있도록 최소 메타데이터(user_id, video_id 등)를 함께 보유한다.
2. 최종 일관성: Vector Store는 Metadata DB로부터 파생된 Projection이며, 부분 실패/지연이 발생할 수 있다. 사용자 노출 정합성은 Metadata DB의 상태(READY) 및 최종 서빙 검증(SOT Validation)으로 보장한다.


#### Model Artifact Files

1. 모델 버전 관리: 임베딩 모델 파일과 버전 메타데이터는 파일 단위의 배포 아티팩트로 관리한다.
2. 버전 식별 기준: 서빙 중 모델 버전의 SOT는 Metadata DB의 릴리스 레코드이다. artifact path는 최초 기동 시 릴리스 레코드가 없을 때의 부트스트랩 기본값으로 사용된다.
3. 서빙 반영 방식: 재색인 완료 후 ML Lifecycle Worker가 Managed Embedding Endpoint와 ModelRelease를 갱신하여 후보 모델을 서빙에 반영한다. 롤백도 동일한 원칙을 따르며, 롤백 대상 모델이 Managed Embedding Endpoint에 실제로 로드되고 readiness를 통과한 뒤에만 ModelRelease를 이전 상태로 복원한다.
4. 후보 산출물 보관: 학습으로 생성된 후보 모델 파일과 관련 버전 정보를 보관한다.


---

### 관리 및 운영 (Admin & Ops)

**목적:** 운영자가 파이프라인/모델 품질을 관리하고 장애 대응 및 모델 운영 절차를 수행

#### Admin Dashboard

* 운영 모니터링: 파이프라인 상태, 실패 현황, 모델 운영 상태를 조회할 수 있는 관리 인터페이스를 제공한다.
* 운영 액션: 재처리, 데이터 삭제 등 운영 액션을 수행한다.
* 모델 운영 지원: MLPipelineRun 진행 상태(피드백 루프) 및 실패 현황을 조회한다. 현재 실행 중인 모델 개선 파이프라인과 최신 데이터셋 기준의 다음 대기 실행이 있는지도 확인할 수 있다. 실패는 대시보드에 표시하고, 조치가 필요한 상태도 함께 확인할 수 있다. 품질 미달(FAIL)과 시스템 오류(ERROR)는 구분하여 표시한다. 장애 시 수동 재트리거 및 롤백 액션을 제공한다.

#### Observability (Logging / Metrics)

* 운영 데이터 수집: 파이프라인 상태, 검색 응답 시간/실패율, 큐 적체량, 피드백 수집 현황 등 운영 판단에 필요한 로그와 지표를 수집/저장하여 Admin Dashboard와 운영 절차에서 활용할 수 있도록 한다.
* 장애 분석 지원: 특정 단계 실패, 검색 오류, 재처리 필요 여부를 판단할 수 있도록 trace_id 기반 로그와 핵심 지표를 제공한다.

#### 주요 연결 관계

* Admin Dashboard → Core API Server (Admin 기능): 동기 HTTP 호출로 파이프라인 상태 조회, 실패 상세 조회, 재처리/강제 삭제를 요청한다.
* ML Lifecycle Worker (자동 연쇄): 전처리 완료 후 학습→평가→재색인→서빙 전환을 자동으로 연쇄 수행한다. Admin Dashboard에서 장애 시 수동 재트리거 가능.
* ML Lifecycle Worker → Model Artifact Files → Managed Embedding Endpoint: 재색인 완료 후 ML Lifecycle Worker가 후보 모델을 서빙에 자동 반영한다.
* 각 백엔드 컴포넌트 → Observability: Core API Server, Search Service, Media & AI Pipeline Worker, ML Lifecycle Worker, Managed Embedding Endpoint가 로그와 메트릭을 push 방식으로 전송한다.

---

## 1.2 시스템 라이프사이클 (상태 전이 흐름)

단일 워커 통합 및 AI 모델 직접 호출 구조를 통해 컴포넌트 간 네트워크 이동 경로가 대폭 간소화되었다. 전체 시스템의 데이터 무결성과 파이프라인 진행 제어는 Metadata DB의 **상태(Status)**를 기준으로 통제된다. 구체적인 I/O 명세는 `2. Data Flow`를 따른다.

**Status 전이 흐름:**
1. **정상 전이:** `PENDING` (요청 인입) → `UPLOADED` (영상 원본 확보) → `PROCESSING` (추출 및 AI 분석 중) → `READY` (검색 가능 상태)
2. **예외 전이:** 진행 중 어느 단계에서든 오류 발생 시 `FAILED`로 전이되며, DB에 `failed_stage`를 기록하여 재시도 시 실패 분류 및 재개 판단 근거로 활용한다.
3. **삭제 전이:** 임의 상태에서 사용자가 삭제 요청 시 `DELETING`으로 전이된다. 이 시점부터 해당 영상은 검색 범위에서 즉시 제외된다. Pipeline Worker가 연쇄 삭제를 완료한 후 레코드를 hard-delete한다.

**모델 개선 사이클 (피드백 루프):**

사용자 피드백을 기반으로 임베딩 모델을 개선하고 프로덕션에 반영하는 운영 사이클이다. 구체적인 데이터 I/O는 2.7~2.8을 따른다.

데이터 수집부터 모델 배포까지 자동으로 진행: 수집 → 전처리(자동 배치) → 학습 → 평가 → 판정 분기 → 재색인 → 서빙 전환 / 실행 제어: 한 번에 하나만 활성 실행 유지, 더 최신 데이터셋 실행이 있으면 기존 대기 실행 대체 / 실패 시 기존 서빙 유지 + Admin Dashboard에 실패 상태 표시

- 수집: 사용자 피드백이 Object Storage의 원본 이벤트 로그로 자동 누적된다. (2.6 참조)
- 전처리: ML Lifecycle Worker가 배치 스케줄에 따라 신규 피드백을 학습용 데이터셋으로 변환한다. 전처리 완료 후 학습 파이프라인을 자동 트리거한다.
- 학습: ML Lifecycle Worker가 자동으로 임베딩 모델 학습을 수행한다.
- 평가: 학습에 이어 후보 모델과 기준 모델의 검색 성능을 학습셋과 분리된 오프라인 평가셋으로 비교 평가한다. 평가셋은 immutable artifact로 버전 관리되며, 평가 결과가 기준을 충족하면 배포 후보로 판정한다.
- 판정 분기: 평가 결과가 기준에 맞으면 재색인을 진행하고, 기준에 미치지 못하면 기존 서빙을 유지하며 Admin Dashboard에 실패 상태를 표시한다.
- 재색인: 후보 모델 전용 인덱스를 먼저 만들고, 재색인 중 새로 들어온 데이터는 기존 서빙을 유지한 채 후보 인덱스에도 추가로 반영한다. 이때 신규 유입 데이터에 대한 색인과 기존 데이터 재색인은 릴리스 레코드를 기준으로 수행한다.
- 서빙 전환: 후보 인덱스가 전환 기준 시각까지의 데이터를 모두 반영했고 readiness도 통과했을 때만 ML Lifecycle Worker가 ModelRelease를 갱신하여 서빙을 전환한다. Search Service의 질의 임베딩은 전환 전까지 active 모델/인덱스만 사용한다. 이전 인덱스는 롤백 대비용으로 보존한다.
- 예외: 각 단계 실패 시 MLPipelineRun에 실패 단계와 유형을 기록하고 파이프라인을 종료한다. 기존 서빙은 유지된다.

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
4. **[STT 변환]** 로컬의 오디오 데이터를 Worker 내부 STT 연동 구현을 통해 외부 음성 인식 서비스로 직접 전송하여 텍스트 및 타임스탬프 스크립트를 반환받는다.
5. **[청킹 및 임베딩]** Worker가 전체 스크립트를 문맥 단위로 청킹하고 키프레임을 매핑한다. 텍스트 청크를 **Managed Embedding Endpoint(자체 배포 모델)**로 직접 전송하여 임베딩 벡터를 반환받는다.
6. **[적재 및 완료]** 스크립트/청크(텍스트, 타임스탬프, 참조)는 Metadata DB(SOT)에 적재하고, 임베딩 벡터는 Vector Store(ANN)에 적재(Upsert)한다. ModelRelease에 candidate 재색인 상태가 열려 있으면 online ingest는 active 인덱스와 candidate 인덱스에 각각 맞는 `model_version`으로 dual-write 한다. 두 저장소 반영 완료 시 status=READY로 갱신한다.

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
- 데이터: 자연어 질의 텍스트, Authorization 토큰

**처리**
1. Reverse Proxy가 요청을 수신하여 Authorization 헤더를 포함한 원본 요청을 그대로 Search Service로 전달
2. Search Service가 내부 미들웨어를 통해 JWT를 직접 검증하고, claim에서 requester_user_id를 추출하여 테넌시 필터에 사용
3. 질의 텍스트를 **Managed Embedding Endpoint**로 직접 보내 임베딩 벡터로 변환한다.
4. Search Service가 먼저 요청자 소유 영상이 0개인지 확인하고, 0개면 `409 NO_VIDEOS_UPLOADED`를 반환한다.
5. Search Service가 요청자 소유 영상 중 `READY`가 아닌 항목이 있는지 확인하고, 있으면 `409 SEARCH_NOT_READY`를 반환한다.
6. Search Service가 Metadata DB(FTS)에서 키워드 후보 Top-K를 조회 (테넌시 적용)
7. Search Service가 Vector Store(ANN) 에서 벡터 후보 Top-K를 조회 (테넌시 적용)
8. Search Service가 키워드/벡터 후보를 병합(RRF)하여 최종 Top-K 후보를 결정
9. Search Service가 Metadata DB(SOT) 를 “서빙 게이트”로 조회하여 (권한/존재 여부(삭제=hard delete)/READY 상태) 검증을 수행하고, 최종 컨텍스트(청크 텍스트/타임스탬프)를 로드한다. 이 단계 이후에도 최종 컨텍스트가 0개면 LLM을 호출하지 않고 `"검색 결과가 없습니다"`를 반환한다.
10. Search Service가 Top-K 컨텍스트 + 질의를 서비스 내부 LLM 인터페이스 구현체에 전달하여 최종 답변과 structured `used_refs`를 생성한다.
11. Search Service가 검색 응답을 Client에 반환한다. 동시에 피드백 검증과 운영 추적에 사용할 수 있도록, 요청 시점의 응답 내용과 활성 모델/인덱스 정보를 포함한 검색 응답 스냅샷을 불변 기록으로 저장한다.

**출력**
- `req_id` + 생성된 답변 + `chunks` → Client 반환
  - `chunks`: SOT 게이트를 통과해 실제 응답 생성에 사용된 최종 청크의 canonical 배열
  - `chunks[].ref`: 답변 본문 `[n]` 인라인 인용과 대응하는 요청 단위 citation 번호
  - `chunks[].used`: 해당 청크가 실제 답변 근거로 사용되었는지 여부

**저장 위치**
- 검색 응답 본문은 실시간으로 생성 및 반환되며 별도로 장기 보관하지 않음.
- 단, 피드백 검증과 운영 감사용으로 `req_id` 기준의 검색 응답 스냅샷을 단기 보존하며, 피드백 수집 시 해당 스냅샷의 핵심 필드가 원본 이벤트에 함께 고정된다.


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
   - 단, 이미 수집된 피드백 이벤트와 이미 만들어진 학습/평가용 데이터셋은 운영 기록으로 그대로 보존한다. 다만 이후 새 데이터셋을 만들 때는 이미 삭제된 영상이나 청크를 다시 사용하지 않는다.
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
1. Client가 검색 응답 단위의 피드백을 Core API Server에 전송한다.
2. Core API Server가 사용자 권한을 검증하고, `req_id`에 대응하는 검색 응답 스냅샷을 조회하여 동일 사용자 요청인지, 허용된 시간 창 내의 피드백인지, 이미 무효화된 요청이 아닌지 확인한다.
3. Core API Server가 검증된 피드백 이벤트에 검색 시점의 질문, 응답 결과, 활성 모델/인덱스 정보 등 피드백 검증과 추적에 필요한 정보를 함께 담아 Feedback Ingestion Pipeline으로 전달한다.
4. Feedback Ingestion Pipeline이 검증된 피드백 이벤트를 원본 로그 형태로 Object Storage에 적재한다. 이 원본 로그는 이후 데이터셋 생성 시 재현 가능한 최소 맥락을 포함해야 한다.

**저장 위치**
| 데이터 | 저장소 |
|--------|--------|
| 검색 응답 단위 피드백 원본 이벤트 로그 | Object Storage |

## 2.7 피드백 데이터셋 생성

**입력**
- 주체: ML Lifecycle Worker (배치 전처리), 운영자
- 데이터: 피드백 원본 이벤트 로그 (Object Storage)

**처리**
1. ML Lifecycle Worker가 정기 배치에 따라 신규 피드백 원본 로그를 읽고, 검색 시점의 불변 서빙 맥락이 포함된 이벤트만 학습 가능한 데이터셋으로 전처리한다.
2. 생성된 데이터셋은 학습 시점의 입력을 다시 추적할 수 있도록 버전 단위로 저장한다. 학습용 데이터셋과 평가용 데이터셋은 분리된 산출물로 관리한다.
3. 전처리 완료 후 학습 파이프라인을 자동 트리거한다. 이미 MLPipelineRun이 실행 중이면 즉시 시작하지 않고, 최신 데이터셋 기준의 다음 실행을 대기 상태로 둔다.

**출력**
- 학습용 데이터셋 생성
- 학습 파이프라인 자동 트리거

**저장 위치**
| 데이터 | 저장소 |
|--------|--------|
| 전처리된 학습 데이터셋 | Object Storage |

## 2.8 모델 재학습 및 재색인

**입력**
- 주체: 운영자, ML Lifecycle Worker
- 데이터: 선택된 학습용 데이터셋 버전 (Object Storage), 평가용 데이터셋 버전 (Object Storage), 후보 모델 아티팩트

**처리**
1. 전처리 완료 후 ML Lifecycle Worker가 최신 학습용 데이터셋으로 후보 임베딩 모델을 학습하고, 결과 모델을 저장한다. 이후 실행 상태 추적을 위해 MLPipelineRun을 생성한다.
2. ML Lifecycle Worker가 후보 모델과 기준 모델의 검색 성능을 별도 평가용 데이터셋으로 비교 평가한다. 평가 결과 요약은 Metadata DB에 저장하고, 상세 결과는 아티팩트로 저장한다.
3. 평가를 통과하면 ML Lifecycle Worker가 후보 모델 전용 인덱스를 별도로 구축한다. 이 과정에서도 사용자 검색은 기존 모델과 인덱스로 계속 제공한다.
4. 후보 인덱스에 전환 기준 시점까지의 데이터가 모두 반영되고, 후보 모델 readiness가 확인되면 ML Lifecycle Worker가 서빙을 전환한다. 이전 모델과 인덱스 정보는 롤백을 위해 보존한다.
5. 평가 실패 또는 처리 중 오류가 발생하면 파이프라인 실행 정보를 기록하고 종료한다. 기존 서빙은 유지되며, 운영자는 Admin Dashboard에서 실패 상태를 확인한다.


**출력**
- 후보 모델 파일 → Model Artifact Files
- 평가 결과 → Metadata DB
- 평가 질의별 상세 아티팩트 → Object Storage
- 재색인된 임베딩 벡터 → Vector Store
- 활성 모델/인덱스 버전 (릴리스 레코드) → Metadata DB

**저장 위치**
| 데이터 | 저장소 |
|--------|--------|
| 후보 모델 파일 + 버전 메타데이터 | Model Artifact Files |
| 평가 결과 집계 | Metadata DB |
| 평가 질의별 상세 JSONL 아티팩트 | Object Storage |
| 재색인된 임베딩 벡터 | Vector Store |
| 활성 모델/인덱스 버전 (릴리스 레코드) | Metadata DB |



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

## 3.5 Feedback Event
검색 응답 단위로 수집되는 원본 피드백 이벤트 로그. Object Storage에 저장되는 논리 데이터 모델이다.
- event_id: 피드백 이벤트 고유 ID
- user_id: 피드백을 남긴 사용자 ID (User 참조)
- req_id: 검색 응답 고유 ID. 피드백의 귀속 단위이며 Search Service가 응답마다 생성한다.
- query_text: 피드백 시점의 질의 텍스트
- rating: 평가 (LIKE / DISLIKE)
- topk_ids: SOT 게이트를 통과해 실제 응답 생성에 사용된 최종 청크 ID 목록 (relevance 순)
- used_ids: LLM이 structured `used_refs`를 통해 실제 참조했다고 보고한 최종 청크 ID 목록
- active_model_version: 피드백이 발생한 시점에 Search Service가 사용한 활성 임베딩 모델 버전
- active_index_name: 피드백이 발생한 시점에 Search Service가 사용한 활성 벡터 인덱스 식별자
- response_snapshot_ref: 필요 시 검색 응답 스냅샷 원본을 다시 조회할 수 있는 참조값
- created_at: 피드백 시각

## 3.6 ModelEvaluation
후보 임베딩 모델과 기준 모델의 검색 성능 비교 평가 1건에 대한 집계 결과
- id: 평가 실행 고유 ID
- candidate_model_version: 평가 대상 후보 모델 버전
- baseline_model_version: 비교 기준 모델 버전
- evaluation_dataset_ref: 학습셋과 분리된 immutable 평가 데이터셋 참조값
- sample_count: 평가에 사용한 질의 수
- status: 평가 실행 상태 
- quality_metrics: 검색 품질 지표 집합 (구체 항목은 Spec에서 정의)
- pass_criteria: 특정 평가에서 PASS/FAIL을 판단할 때 사용한 기준
- overall_decision: quality_metrics와 pass_criteria를 바탕으로 내린 최종 판정 (PASS / FAIL)
- fail_reason: 평가 실패 시 원인 요약
- created_at: 레코드 생성 시각

## 3.7 ModelEvaluationDetail Artifact
평가 실행 1건에 대응하는 질의별 상세 비교 결과 아티팩트. Metadata DB 테이블이 아니라 별도 파일 아티팩트로 저장한다.
- evaluation_id: 상위 평가 실행 ID (ModelEvaluation 참조)
- storage_path: 질의별 상세 결과 JSONL 아티팩트 경로
- format: 저장 포맷 (JSONL)
- description: 질의 텍스트, 기대 결과, 반환 결과, 질의별 검색 품질 지표를 포함하며 필요 시 운영 절차에서 조회한다.
- created_at: 아티팩트 생성 시각

## 3.8 VectorIndexEntry (Derived; Vector Store)
- index_name: 모델 버전별 물리 분리 인덱스 식별자. 서빙 대상 인덱스는 현재 활성 모델 버전 기준으로 결정된다.
- chunk_id: Chunk 고유 ID (SOT의 Chunk.id와 동일 키)
- user_id: 테넌시 필터용
- video_id: 스코프 필터용
- embedding_vector: 임베딩 벡터
- embedding_model_version: 모델 버전
- created_at: 적재 시각

## 3.9 MLPipelineRun
ML 피드백 루프 파이프라인 실행 1회에 대한 추적 레코드
- id: 실행 고유 ID
- status: 현재 진행 상태
- failed_stage: 실패 단계
- failure_type: 실패 유형
- candidate_model_version: 이번 실행의 후보 모델 버전
- candidate_index_name: 이번 실행의 후보 인덱스 식별자
- dataset_version: 학습에 사용한 데이터셋 버전
- evaluation_id: 연결된 평가 결과 식별자
- cutover_time: 서빙 전환 전에 반영이 완료되어야 하는 기준 시각
- superseded_by_run_id: 더 최신 실행으로 대체되었을 때의 실행 ID
- failure_reason: 실패 원인 요약
- created_at / updated_at

## 3.10 ModelRelease
모델 서빙 상태의 SOT. 현재 활성 서빙 조합, 전환 중인 후보 조합, 롤백 대상을 관리하는 릴리스 레코드
- release_status: 현재 모델 전환 진행 상태
- active_model_version: 현재 활성 모델 버전
- active_index_name: 현재 활성 인덱스 식별자
- candidate_model_version: 전환 중인 후보 모델 버전
- candidate_index_name: 전환 중인 후보 인덱스 식별자
- rollback_model_version: 롤백 대상 모델 버전
- rollback_index_name: 롤백 대상 인덱스 식별자
- candidate_ready_at: 후보 조합의 readiness 확인 시각
- switched_at: 마지막 서빙 전환 시각

## 3.11 Async Message Contract
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
- `TRAINING_REQUEST`: Payload 추가 필드 없음. 학습 대상 데이터셋 버전은 자동 선택되며, 워커는 해당 버전을 조회하여 학습을 수행한다.

---

