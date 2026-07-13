# [ADR-016] 큐 컨슈머 워커의 Cloud Run 실행 형태

* **상태 (Status):** 제안됨(Proposed)
* **날짜 (Date):** 2026-07-09

## 1. 배경 및 문제 상황 (Context and Problem Statement)

* **상황:** `pipeline-worker`와 `feedback-loop-*` 워커 7개는 pgmq 큐를 소비한다. 모두 Cloud Run **서비스(service)** 로 배포되어 있다.
* **문제:** 이 워커들은 HTTP 요청을 받지 않는다. `src/main.py`의 프로덕션 부트스트랩은 `consumer.run_forever()`를 호출해 1초 간격으로 큐를 폴링하는 무한 루프를 돈다. `utils/health_server.py`는 그 주석이 밝히듯 "순수 큐 소비자는 포트를 열지 않아 startup probe가 실패"하기 때문에 붙인 껍데기 HTTP 서버다. `core-api`가 워커를 HTTP로 호출하는 경로도 없다.

  Cloud Run 서비스는 요청 기반으로 설계된 리소스다. 여기에 pull 방식 워커를 얹으면 두 가지가 강제된다.

  1. `cpu-throttling=false` (인스턴스 기반 과금) — 요청 밖에서 CPU가 돌아야 하므로.
  2. `min-instances >= 1` — 요청이 없으면 인스턴스가 0으로 줄어들어 폴링 루프가 죽으므로.

  즉 **min-instances는 성능 옵션이 아니라 워커의 생존 조건**이다. 현재 실배포는 `min=0`이므로 **워커가 큐를 소비하지 못하고 있을 가능성이 높다(미검증).** 2026-07-08 "영상 업로드 무한 pending" 문제와 원인이 같을 수 있다.

  비용 측면에서는 2026-06-01 ~ 07-09 결제 기준 `Services CPU/Memory (Instance-based billing)` 합계 11,050원이 발생했다. 인스턴스가 살아 있던 89.2시간 전체에 CPU 요금이 붙은 결과다.

* **목표:** 워커가 안정적으로 큐를 소비하면서, 인스턴스 기반 과금의 비용과 `min>=1` 의존을 줄인다.

## 2. 고려한 옵션들 (Considered Options)

### 1. 현행 유지 — Cloud Run 서비스 + `cpu-throttling=false` + `min>=1`

껍데기 health 서버를 유지하고 최소 인스턴스를 1 이상으로 고정한다.

**Pros**
* 코드 변경이 없다.
* 구글이 공식 문서에서 인정하는 조합이다. 인스턴스 기반 과금과 최소 인스턴스를 함께 쓰면 Pub/Sub streaming pull, Kafka 컨슈머 그룹 같은 백그라운드 처리가 가능하다고 명시한다.

**Cons**
* 요청이 하나도 없는데 HTTP 서버를 띄우는 구조적 군더더기가 남는다.
* `min>=1`이 생존 조건인데 코드나 문서 어디에도 그 사실이 드러나 있지 않다. 비용을 아끼려 `min=0`으로 내리면 워커가 조용히 멈춘다. 실제로 지금 그 상태다.
* 유휴 폴링 시간까지 전부 과금된다.

### 2. Cloud Run worker pools로 이관

구글이 "HTTP 없는 pull 기반 백그라운드 처리"용으로 제공하는 별도 리소스 타입이다. 공개 HTTP 엔드포인트가 필요 없고, 큐에서 계속 메시지를 당겨오는 워크로드를 전제로 만들어졌다.

**Pros**
* 코드 변경이 최소다. 워커는 이미 HTTP를 쓰지 않으므로 `start_health_server()` 호출 제거와 배포 리소스 교체로 끝난다.
* 인스턴스 기반 과금 서비스 대비 CPU/메모리 요금이 최대 40% 저렴하다.
* `min>=1`이 생존 조건이라는 암묵적 제약이 사라진다. 워커 풀은 요청 없이 계속 도는 것을 전제로 한다.
* 껍데기 health 서버라는 설계상의 거짓말을 제거할 수 있다.

**Cons**
* 여전히 인스턴스 단위 과금이다. 요청 기반 과금이 되는 것은 아니다.
* Terraform 리소스와 배포 파이프라인을 새로 써야 한다.
* 워커 풀은 서비스보다 늦게 나온 기능이라 운영 사례와 참고 자료가 상대적으로 적다.

### 3. 초인종(doorbell) 방식 — pgmq 유지 + Cloud Tasks로 HTTP 트리거

pgmq를 큐의 SOT로 그대로 두고, `core-api`가 메시지를 넣은 뒤 Cloud Tasks로 워커에 HTTP 요청 하나를 보낸다. 워커는 그 요청 안에서 큐를 비우고 응답한다.

**Pros**
* 요청 안에서 일하므로 `cpu-throttling=true` + `min=0`이 성립한다. 진짜 요청 기반 과금이 된다.
* 브로커를 교체하지 않는다. pgmq의 가시성 타임아웃과 재시도는 그대로 살아 있고, Cloud Tasks는 깨우는 역할만 한다.
* 이미 구현된 `consumer.run_until_empty()`를 그대로 재사용할 수 있다.

**Cons**
* Cloud Run 요청 타임아웃(최대 60분) 안에 영상 처리가 끝나야 한다. 긴 영상에서 제약이 된다.
* 요청 기반 과금이어도 **영상을 처리하는 동안의 CPU 요금은 동일하다.** 절감되는 것은 유휴 폴링분과 처리 후 스케일다운까지의 유휴분뿐이므로, 89.2시간 중 실제 처리 시간 비중을 모르면 절감액을 추정할 수 없다.
* 발행 경로에 컴포넌트가 하나 늘고, 초인종 유실 시 메시지가 처리되지 않는 새 실패 모드가 생긴다.

### 4. Cloud Run Jobs + Cloud Scheduler

주기적으로 잡을 띄워 큐를 비우고 종료한다.

**Pros**
* 실행 시간만 과금된다.
* `run_until_empty()`를 그대로 쓸 수 있다.

**Cons**
* 스케줄 주기만큼 처리 지연이 생긴다. 업로드 직후 처리라는 사용자 경험과 맞지 않는다.
* 큐가 빈 상태에서도 잡이 뜨고 종료되는 낭비가 반복된다.

## 3. 결정 사항 (Decision Outcome)

**아직 결정하지 않는다. 아래 순서로 선행 조치와 측정을 마친 뒤 확정한다.**

현재 기울어진 방향은 **옵션 2(worker pools)** 다. 코드 변경이 가장 적고, 비용 단가가 확실히 낮아지며, `min>=1` 생존 조건이라는 숨은 제약을 구조적으로 없애기 때문이다.

옵션 3은 요청 타임아웃과 절감 폭 불확실성 때문에 보류한다. 옵션 4는 지연 시간 때문에 채택하지 않는다.

**결정 전에 반드시 선행할 것**

1. **워커 생존 여부 확인.** 지금 `min=0` 상태에서 큐에 메시지를 넣고 처리되는지 확인한다. 처리되지 않으면 장애이므로 비용 논의보다 먼저 복구한다.
2. **Terraform 드리프트 해소.** `modules/cloud_run_service/variables.tf`의 `cpu_idle` 기본값은 `true`인데 실배포 워커 7개는 `cpu-throttling=false`다. 배포 리비전에 `client-name=gcloud` 어노테이션이 붙어 있어 수동 배포로 생긴 드리프트다. **지금 상태로 `terraform apply`를 하면 워커 7개가 요청 기반으로 되돌아가 큐를 읽지 못한다.** 코드와 실배포를 일치시키는 것이 최우선이다.
3. **실제 처리 시간 비중 측정.** 인스턴스 생존 89.2시간 중 실제 영상 처리에 쓴 시간을 로그로 산출한다. 이 값이 옵션 2와 옵션 3의 절감 폭을 가른다.

## 4. 결정된 설계 원칙 (Decision Details)

* 큐 컨슈머는 **요청 기반 리소스 위에 올리지 않는다.** HTTP 요청을 받지 않는 컴포넌트에 껍데기 HTTP 서버를 붙여 요청 기반 리소스에 맞추는 방식은 임시방편으로만 허용하고, 그 사실을 코드와 배포 설정 양쪽에 명시한다.
* 인스턴스의 **생존 조건을 배포 파라미터에 의존하게 두지 않는다.** `min-instances` 같은 값은 성능 조절용이어야 하며, 그 값이 0이 되었을 때 컴포넌트가 죽는다면 그것은 실행 형태를 잘못 고른 것이다.
* 비용 최적화 조치는 **해당 컴포넌트의 생존 조건을 먼저 문서화한 뒤** 적용한다.

## 5. 긍정적 효과 (Positive Consequences)

* `min=0`으로 되돌렸을 때 워커가 조용히 멈추는 사고를 구조적으로 차단한다.
* worker pools 채택 시 워커 CPU/메모리 요금이 최대 40% 절감된다.
* `health_server.py`라는 우회 코드를 제거해, 컴포넌트의 실제 성격(HTTP를 쓰지 않는 pull 워커)이 코드에 그대로 드러난다.

## 6. 부정적 효과 및 위험 요소 (Negative Consequences)

* 지금 `terraform apply`를 하면 워커 7개가 요청 기반으로 되돌아가 큐 소비가 멈춘다.
  * **대응:** 결정과 무관하게, Terraform에 워커용 `cpu_idle = false`를 즉시 명시해 실배포와 일치시킨다.
* 워커가 이미 멈춰 있다면 큐에 미처리 메시지가 쌓여 있을 수 있다.
  * **대응:** 큐 깊이와 `PROCESSING` 상태로 멈춘 영상을 함께 점검한다. 2026-07-08 무한 pending 건과 연관 여부를 확인한다.
* worker pools는 서비스보다 늦게 나온 기능이라 운영 중 예상 못 한 제약을 만날 수 있다.
  * **대응:** `feedback-loop-*` 워커 하나를 먼저 이관해 검증한 뒤 `pipeline-worker`를 옮긴다.
* 절감 폭이 기대보다 작을 수 있다. 인스턴스 생존 시간의 대부분이 실제 처리 시간이라면 방식 변경으로 줄어드는 몫이 적다.
  * **대응:** 선행 조치 3번의 측정 결과를 근거로 이관 여부를 재검토한다. worker pools는 단가 자체가 낮아 처리 시간 비중과 무관하게 효과가 있다.

## 7. 결정 이후 후속 결과 (Consequences)

* (최초 작성. 결정 확정 시 이어서 기록한다.)

## 참고

* [Billing settings for services | Cloud Run](https://docs.cloud.google.com/run/docs/configuring/billing-settings)
* [Use Cloud Run "always-on" CPU allocation for background work | Google Cloud Blog](https://cloud.google.com/blog/topics/developers-practitioners/use-cloud-run-always-cpu-allocation-background-work)
* [Exploring Cloud Run worker pools and Kafka Autoscaler | Google Cloud Blog](https://cloud.google.com/blog/products/serverless/exploring-cloud-run-worker-pools-and-kafka-autoscaler)
* [Deploy worker pools to Cloud Run](https://docs.cloud.google.com/run/docs/deploy-worker-pools)
* [About instance autoscaling in Cloud Run services](https://docs.cloud.google.com/run/docs/about-instance-autoscaling)
* 비용 근거: `agent_memory/문제 정의 및 해결 과정 log/2026-07-09-gcp-compute-engine-cost-spike-문제상황.md`
