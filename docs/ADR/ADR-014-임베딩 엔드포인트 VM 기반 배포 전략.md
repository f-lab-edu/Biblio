# [ADR-014] 임베딩 엔드포인트 배포환경 전략

* **상태 (Status):** 승인됨(Accepted)
* **날짜 (Date):** 2026-06-15

## 1. 배경 및 문제 상황 (Context and Problem Statement)

* **상황:** Biblio 성능 테스트 환경은 검색과 색인에 사용할 임베딩 모델을 배포 환경에서 서빙해야 한다. 현재 `managed-embedding-endpoint`는 Docker image로 빌드할 수 있고, 로컬 모델 경로를 기준으로 모델을 로드하는 구조를 갖고 있다.
* **문제:** Cloud Run에 그대로 배포하면 모델 파일을 어디에 저장하고, 새 인스턴스가 뜰 때 모델을 어떻게 확보할지 결정해야 한다. 또한 현재 Cloud Run 메모리 설정은 512MiB 수준이라 `bge-m3` CPU 모델 1개도 안정적으로 로드하기 어렵고, active, previous, candidate처럼 여러 모델 버전을 런타임에 올리는 구조도 감당하기 어렵다.
* **목표:** 현재 코드 구조를 크게 바꾸지 않으면서, 모델 파일 저장 위치와 실행 위치를 명확히 정한다. 동시에 성능 테스트 환경에서 비용을 통제하고, 모델 버전 전환과 롤백을 검증할 수 있어야 한다.

## 2. 고려한 옵션들 (Considered Options)

### 1. Cloud Run에서 직접 해결

`managed-embedding-endpoint`를 Cloud Run 서비스로 유지한다. 모델 원본은 GCS 또는 Hugging Face에 두고, Cloud Run 인스턴스가 시작될 때 모델 파일을 확보한 뒤 메모리에 로드한다.

**Pros**
* 현재 Terraform 배포 방향과 가장 가깝다.
* Cloud Run의 배포, revision, 기본 로그 수집을 그대로 사용할 수 있다.
* 요청이 없을 때 scale to zero로 비용을 줄일 수 있다.

**Cons**
* 인스턴스가 새로 뜰 때 모델 파일 확보와 모델 로드가 반복될 수 있다.
* 모델이 크면 cold start가 길어진다.
* 여러 모델 버전을 동시에 올리면 Cloud Run 메모리 비용이 커진다.
* Cloud Run 로컬 캐시는 인스턴스 생명주기에 묶이므로 모델 캐시가 안정적으로 남는다고 보기 어렵다.

### 2. GCE VM에서 Docker image로 직접 운영

`managed-embedding-endpoint` Docker image를 GCE VM에서 실행한다. 모델 원본은 GCS에 두고, VM의 persistent disk는 실행용 모델 캐시로 사용한다.

**Pros**
* 현재 endpoint의 로컬 모델 경로 기반 설계와 잘 맞는다.
* VM을 껐다 켜도 persistent disk의 모델 파일은 남는다.
* 모델 파일을 매번 다시 다운로드할 가능성을 줄일 수 있다.
* CPU, RAM, 디스크 크기를 모델 serving 기준으로 직접 고를 수 있다.
* VM을 stop하면 CPU/RAM 비용을 멈출 수 있어 성능 테스트 비용을 통제하기 쉽다.

**Cons**
* VM 운영 책임이 생긴다.
* Docker 컨테이너 자동 시작, 재시작 정책, 로그 수집, health check를 직접 설계해야 한다.
* VM이 켜져 있는 동안에는 요청이 없어도 비용이 발생한다.
* 모델 캐시 삭제 정책을 정하지 않으면 `/models`에 버전이 계속 쌓인다.

### 3. Vertex AI 같은 관리형 ML 인프라 사용

모델 serving을 Vertex AI Endpoint와 Model Registry 같은 관리형 ML 인프라에 맡긴다. 애플리케이션은 Vertex AI prediction API를 호출해 embedding을 받는다.

**Pros**
* 모델 배포, 버전 관리, traffic split, 롤백을 관리형 기능으로 처리할 수 있다.
* 운영형 ML serving으로 확장하기 좋다.
* GPU나 고성능 serving이 필요해질 때 선택지가 넓다.

**Cons**
* 현재 FastAPI 기반 endpoint를 제거하거나 Vertex AI 호출 wrapper로 축소해야 한다.
* 기존 `model_release` 중심의 모델 전환 흐름과 Vertex AI endpoint 상태를 어떻게 맞출지 새로 설계해야 한다.
* custom container가 필요할 가능성이 있다.
* 현재 성능 테스트 단계에서는 변경 범위와 학습 비용이 크다.

## 3. 결정 사항 (Decision Outcome)

* **2번, GCE VM에서 Docker image로 직접 운영하는 방식을 선택한다.**
* **1번 Cloud Run 직접 운영은 향후 서버리스 운영이 더 중요해질 때 재검토한다.**
* **3번 Vertex AI는 운영형 ML serving이 필요해지는 시점에 재검토한다.**

**이유**
* 현재 `managed-embedding-endpoint`는 로컬 모델 경로를 기준으로 동작하므로, VM persistent disk를 실행용 모델 캐시로 쓰는 방식과 잘 맞는다.
* `bge-m3` CPU 모델은 Cloud Run 512MiB 설정으로 감당하기 어렵고, 여러 모델 버전 로드까지 고려하면 실행 환경의 RAM과 디스크를 직접 고르는 편이 단순하다.
* 성능 테스트 단계에서는 VM을 필요한 시간에만 켜고 끌 수 있어 비용을 통제하기 쉽다.
* Vertex AI는 모델 운영 기능이 강하지만, 현재 구조를 크게 바꾸므로 이번 배포 검증의 1차 선택지로는 무겁다.

## 4. 결정된 설계 원칙 (Decision Details)

* 모델 원본은 GCS의 ML artifact 저장소에 둔다.
* VM의 persistent disk는 모델 원본이 아니라 실행용 캐시로 사용한다.
* `managed-embedding-endpoint`는 Docker image로 배포하고, VM에서 컨테이너로 실행한다.
* 모델 파일은 VM의 모델 캐시 경로에 버전별 디렉터리로 보관한다.
* 모델 전환은 DB의 `model_release`와 endpoint reload 절차를 통해 처리한다.
* endpoint는 active, previous, candidate 모델 버전을 필요에 따라 로드할 수 있어야 한다.
* Cloud Run 서비스들은 VPC 내부 경로로 embedding endpoint를 호출한다.
* VM은 public inbound 없이 운영하는 것을 기본으로 한다.
* 모델 캐시 정리 정책과 VM 운영 정책은 별도 구현 계획에서 구체화한다.

## 5. 긍정적 효과 (Positive Consequences)

* 현재 endpoint 코드를 크게 바꾸지 않고 배포 환경에 맞출 수 있다.
* 모델 파일 보관과 모델 로딩 경로가 명확해진다.
* Cloud Run의 임시 캐시와 인스턴스 재시작에 따른 모델 다운로드 반복을 줄일 수 있다.
* RAM과 디스크를 모델 크기에 맞게 직접 조정할 수 있다.
* 성능 테스트 후 VM을 stop해 CPU/RAM 비용을 줄일 수 있다.

## 6. 부정적 효과 및 위험 요소 (Negative Consequences)

* VM 운영 책임이 추가된다.
  * **대응:** Docker 컨테이너 자동 시작, 재시작 정책, 로그 수집, health check를 구현 범위에 포함한다.
* 모델 버전이 VM 디스크에 계속 쌓일 수 있다.
  * **대응:** GCS를 원본 저장소로 유지하고, VM에는 active, previous, candidate와 최근 여유분만 남기는 캐시 정리 정책을 둔다.
* active, previous, candidate를 동시에 로드하면 RAM 사용량이 커진다.
  * **대응:** 초기 machine type은 세 모델 동시 로드를 감당할 수 있는 RAM 기준으로 산정하고, smoke test 단계에서는 active만 로드하는 축소 운영도 허용한다.
* VM 장애 시 embedding 요청이 실패할 수 있다.
  * **대응:** VM health check, 컨테이너 재시작 정책, 장애 알림을 추가하고, 운영 전환 시 Managed Instance Group 또는 Vertex AI 재검토 기준을 둔다.
* Vertex AI가 제공하는 traffic split과 관리형 모델 배포 기능은 이번 결정에서 사용하지 않는다.
  * **대응:** 성능 테스트 이후 모델 배포 빈도, 운영 장애 대응 비용, GPU 필요성이 커지면 Vertex AI 전환을 별도 ADR로 다시 평가한다.

## 7. 결정 이후 후속 결과 (Consequences)

* Terraform에서 `managed-embedding-endpoint`를 Cloud Run 배포 대상에서 제외하거나, VM 기반 배포 대상으로 분리해야 한다.
* embedding VM, model persistent disk, Cloud Run에서 embedding VM으로 가는 내부 네트워크 경로가 필요하다.
* 모델 원본을 GCS에 업로드하고 VM 캐시 경로로 동기화하는 배포 절차가 필요하다.
* `EMBEDDING_API_URL`은 Cloud Run URL이 아니라 embedding VM의 내부 주소를 가리키도록 조정해야 한다.
* VM 운영 정책과 모델 캐시 정리 정책은 구현 계획에서 확정한다.
