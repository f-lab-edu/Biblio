# [ADR-013] Cloud Run과 VM 간 사설 네트워크 구성

* **상태 (Status):** 완료(Done)
* **날짜 (Date):** 2026-06-14

## 1. 배경 및 문제 상황 (Context and Problem Statement)

* **상황:** Biblio 성능 테스트 환경은 Cloud Run에서 실행되는 API, worker, job이 GCE VM의 PostgreSQL과 임베딩 엔드포인트에 접속해야 한다. PostgreSQL은 `pgvector`와 `PGMQ`를 사용하기 위해 VM에 배포하고, 임베딩 엔드포인트도 모델 파일과 메모리 요구사항 때문에 VM에서 실행한다.
* **문제:** Cloud Run과 VM 사이의 연결을 public IP로 구성하면 빠르게 연결할 수 있지만 데이터베이스와 임베딩 포트가 외부 네트워크에 노출될 수 있다. private IP를 사용하려면 Cloud Run의 VPC 연결 방식, subnet 분리 수준, 방화벽 허용 범위와 외부 IP가 없는 VM의 outbound 경로를 함께 결정해야 한다.
* **목표:** Cloud Run과 VM은 VPC 내부 private IP로 통신한다. VM에는 external IP를 할당하지 않고, 필요한 포트만 허용한다. VM의 패키지 설치와 외부 서비스 접근에 필요한 outbound는 별도 경로로 제공한다.

## 2. 고려한 옵션들 (Considered Options)

### 1. Cloud Run과 VM의 통신 경로

#### 옵션 A. VM public IP를 통한 연결

VM에 external IP를 할당하고 Cloud Run이 public 주소로 PostgreSQL과 임베딩 엔드포인트를 호출한다.

**Pros**
* 별도의 VPC 연결 없이 구성할 수 있다.
* 초기 연결과 장애 확인이 단순하다.

**Cons**
* PostgreSQL과 임베딩 엔드포인트가 외부 네트워크에 노출될 수 있다.
* 방화벽과 인증 설정이 잘못되면 서비스 포트가 공개될 위험이 있다.
* 내부 서비스 통신이 인터넷 경로에 의존한다.

#### 옵션 B. VPC 내부 private IP를 통한 연결

Cloud Run을 VPC에 연결하고 PostgreSQL VM과 임베딩 VM의 private IP를 사용한다.

**Pros**
* 데이터베이스와 임베딩 포트를 외부에 공개하지 않아도 된다.
* 서비스 간 통신 경로를 VPC 내부로 제한할 수 있다.
* 방화벽에서 호출 출발지와 대상 포트를 명확하게 제한할 수 있다.

**Cons**
* Cloud Run의 VPC 연결과 subnet 구성이 필요하다.
* 네트워크 식별자, CIDR, 방화벽 규칙이 맞지 않으면 연결 장애가 발생한다.

### 2. Cloud Run의 VPC 연결 방식

#### 옵션 A. Serverless VPC Access connector

별도 connector 자원을 만들고 Cloud Run 트래픽을 VPC로 전달한다.

**Pros**
* 기존 서버리스 환경에서 널리 사용된 방식이다.
* connector 단위로 네트워크 연결을 관리할 수 있다.

**Cons**
* connector 자원을 별도로 배포하고 운영해야 한다.
* connector의 처리 용량과 인스턴스 비용을 관리해야 한다.
* 현재 환경에서는 Cloud Run과 VPC 사이에 불필요한 중간 자원이 추가된다.

#### 옵션 B. Direct VPC egress

Cloud Run 서비스, worker, job에 VPC network와 subnet을 직접 연결한다.

**Pros**
* 별도 connector 없이 Cloud Run을 VPC subnet에 연결할 수 있다.
* Terraform 자원과 운영 구성이 단순해진다.
* Cloud Run에서 VM private IP로 직접 접근할 수 있다.

**Cons**
* Cloud Run이 사용할 subnet의 주소 공간을 미리 확보해야 한다.
* network와 subnetwork 식별자를 정확하게 전달해야 한다.
* 해당 기능을 지원하는 리전과 플랫폼 조건을 확인해야 한다.

### 3. VPC와 subnet 분리 수준

#### 옵션 A. default VPC 또는 단일 subnet 사용

기존 default VPC를 사용하거나 Cloud Run과 VM을 하나의 subnet에 배치한다.

**Pros**
* 생성할 네트워크 자원이 적다.
* 초기 구성이 빠르고 단순하다.

**Cons**
* 서비스 종류별 네트워크 경계가 분명하지 않다.
* 방화벽 허용 범위가 넓어지기 쉽다.
* 다른 환경이나 자원과 주소 대역이 충돌할 가능성이 있다.

#### 옵션 B. 전용 VPC와 역할별 subnet 분리

Biblio 전용 custom mode VPC를 만들고 Cloud Run, PostgreSQL VM, 임베딩 VM에 각각 subnet을 할당한다.

**Pros**
* 실행 환경별 주소 범위와 방화벽 경계를 명확하게 나눌 수 있다.
* PostgreSQL과 임베딩 엔드포인트에 필요한 호출 경로만 허용할 수 있다.
* 환경 삭제와 재생성 범위를 Biblio 인프라 안으로 제한할 수 있다.

**Cons**
* VPC, subnet, 방화벽 규칙이 늘어난다.
* CIDR 범위와 자원 간 연결 관계를 함께 관리해야 한다.

### 4. 외부 IP가 없는 VM의 outbound 방식

#### 옵션 A. VM에 external IP 할당

PostgreSQL VM과 임베딩 VM에 external IP를 붙여 패키지 저장소, GCS, Artifact Registry 등에 접근한다.

**Pros**
* 별도 NAT 자원 없이 외부 통신이 가능하다.
* 초기 부팅과 장애 확인이 단순하다.

**Cons**
* VM에 public network interface가 생긴다.
* inbound 방화벽 설정 오류가 외부 노출로 이어질 수 있다.
* external IP 관리가 추가된다.

#### 옵션 B. Cloud NAT 사용

VM에는 private IP만 할당하고, 지정된 VM subnet의 outbound 트래픽을 Cloud NAT로 전달한다.

**Pros**
* VM에 external IP를 할당하지 않고 외부로 나갈 수 있다.
* 외부에서 VM으로 직접 연결되는 inbound 경로가 생기지 않는다.
* outbound 대상 subnet을 명시적으로 제한할 수 있다.

**Cons**
* Cloud Router와 Cloud NAT 자원을 운영해야 한다.
* NAT 자체 비용과 로그·장애 확인 지점이 추가된다.

## 3. 결정 사항 (Decision Outcome)

* **VPC 내부 private IP 통신을 선택한다.**
* **Cloud Run의 VPC 연결은 Direct VPC egress를 사용한다.**
* **전용 custom mode VPC를 만들고 Cloud Run, PostgreSQL, 임베딩 VM subnet을 분리한다.**
* **PostgreSQL VM과 임베딩 VM의 outbound는 Cloud NAT를 사용한다.**
* **Serverless VPC Access connector와 VM external IP 방식은 현 단계에서 사용하지 않는다.**

**이유**
* PostgreSQL과 임베딩 엔드포인트를 외부에 공개하지 않고 Cloud Run에서 접근할 수 있다.
* Direct VPC egress는 별도 connector 없이 현재 Cloud Run 서비스, worker, job을 같은 방식으로 연결할 수 있다.
* 역할별 subnet을 사용하면 데이터베이스와 임베딩 포트의 허용 출발지를 명확하게 제한할 수 있다.
* Cloud NAT는 VM의 public inbound 경로를 만들지 않으면서 부팅과 운영에 필요한 outbound를 제공한다.

## 4. 결정된 설계 원칙 (Decision Details)

* Cloud Run 서비스, worker, job은 Cloud Run 전용 subnet에 Direct VPC egress로 연결한다.
* VPC로 보내는 트래픽은 private 주소 범위로 제한한다.
* PostgreSQL과 임베딩 엔드포인트 주소는 VM의 private IP를 사용한다.
* PostgreSQL `tcp:5432`는 Cloud Run subnet과 임베딩 VM subnet에서만 허용한다.
* 임베딩 엔드포인트 `tcp:8000`은 Cloud Run subnet에서만 허용한다.
* PostgreSQL VM과 임베딩 VM에는 external IP를 할당하지 않는다.
* Cloud NAT는 PostgreSQL과 임베딩 VM subnet의 outbound만 처리한다.
* VM 운영자 접속은 IAP TCP forwarding을 사용하고, IAP 주소 범위에서 오는 `tcp:22`만 허용한다.
* 방화벽 대상은 VM별 network tag로 제한한다.

## 5. 긍정적 효과 (Positive Consequences)

* PostgreSQL과 임베딩 엔드포인트를 public network에 공개하지 않는다.
* Cloud Run과 VM의 내부 통신 경로가 private IP로 통일된다.
* 데이터베이스와 임베딩 포트의 접근 범위를 역할별 subnet으로 제한할 수 있다.
* Cloud Run 서비스, worker, job이 같은 VPC 연결 방식을 재사용한다.
* VM은 external IP 없이도 패키지 설치, GCS, Artifact Registry 같은 외부 서비스에 접근할 수 있다.
* IAP를 통해 외부 IP 없는 VM에도 운영자가 제한적으로 접속할 수 있다.

## 6. 부정적 효과 및 위험 요소 (Negative Consequences)

* VPC, subnet, Cloud Router, Cloud NAT와 방화벽을 함께 운영해야 한다.
  * **대응:** 네트워크 자원을 하나의 Terraform module에서 관리하고 환경에서는 module output만 사용한다.
* CIDR이나 방화벽 출발지 범위가 실제 실행 자원과 다르면 내부 연결이 실패한다.
  * **대응:** Cloud Run과 VM subnet을 고정된 입력값으로 관리하고, 방화벽도 같은 값을 참조하게 한다.
* Direct VPC egress에 잘못된 network 식별자를 전달하면 Cloud Run 배포 또는 연결이 실패한다.
  * **대응:** Cloud Run module에는 VPC와 subnet의 resource ID를 전달하고 Terraform 검증과 배포 smoke test로 확인한다.
* Cloud NAT 장애나 설정 누락 시 VM 부팅 과정의 패키지 설치와 이미지 다운로드가 실패할 수 있다.
  * **대응:** NAT 대상 subnet을 Terraform에 명시하고 VM startup log와 serial console에서 실패 원인을 확인한다.
* IAP SSH를 사용하려면 운영자 IAM 권한과 IAP 경로가 필요하다.
  * **대응:** 외부 SSH 포트를 열지 않고, 운영 절차와 필요한 권한을 runbook으로 관리한다.

## 7. 결정 이후 후속 결과 (Consequences)

* 전용 VPC와 Cloud Run, PostgreSQL, 임베딩 subnet을 Terraform으로 구성했다.
* Cloud Run 서비스, worker, job에 Direct VPC egress를 적용했다.
* PostgreSQL과 임베딩 VM은 external IP 없이 private IP만 사용하도록 배포했다.
* PostgreSQL `5432`, 임베딩 엔드포인트 `8000`, IAP SSH `22`에 대한 대상별 방화벽 규칙을 추가했다.
* PostgreSQL과 임베딩 VM subnet을 Cloud NAT 대상에 포함했다.
* 배포 환경에서 Cloud Run의 PostgreSQL 접속, 임베딩 호출과 IAP SSH 접속을 확인했다.
