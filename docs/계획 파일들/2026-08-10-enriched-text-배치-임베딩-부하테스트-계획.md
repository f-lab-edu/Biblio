# Enriched text 배치 임베딩 부하테스트 계획

- 작성일: 2026-08-10
- 대상 이슈: #104
- 상태: 스트레스 테스트 기능 구현 완료 · 실제 실행 대기
- 테스트 대상: batch embedding VM의 `/embed`

## 1. 목표와 현재 상태

목표는 두 가지다.

1. 현재 VM이 Worker 최대 동시성에서 안정적으로 처리할 수 있는 양을 확인한다.
2. 이후 batch size·동시성·입력 분할 구조를 개선할 때 비교할 기준선을 남긴다.

현재까지 완료한 작업은 다음과 같다.

- [x] 실제 `enriched_text` 길이와 형태 조사
- [x] API·모델 입력 제한 확인
- [x] 원문을 포함하지 않는 합성 fixture 생성·검증
- [x] 정상 입력과 512 token 초과 입력 smoke
- [x] 64~512 token 길이별 기준 측정
- [x] VM 전용 스트레스 테스트 S1~S4 구현
- [ ] S1~S4 실제 실행과 결과 분석
- [ ] batch size·동시성 탐색
- [ ] 개선안 선정

## 2. 범위

실행 경로는 다음과 같다.

```text
k6 runner → batch embedding VM /embed
```

포함하는 것은 VM의 처리량·응답시간·대기·503·재시도·자원 사용량·회복 여부다.

다음 항목은 포함하지 않는다.

- Core API, PGMQ, Pipeline Worker, DB를 실제로 연결한 전체 파이프라인
- 영상 업로드, STT, Vision, GCS
- queue visibility, 영상별 완료시간, videos/min
- 검색 품질과 임베딩 유사도 평가

따라서 결과는 **batch embedding VM의 용량**으로만 해석한다. 실제 영상 처리량이나 PGMQ 적체 해소 속도로 바꾸어 보고하지 않는다.

## 3. 테스트 전제

| 항목 | 현재 live 값 | 테스트 적용 |
|---|---:|---|
| Worker 최대 instance | 1 | 스트레스 실행 전 다시 확인 |
| `WORKER_CONCURRENCY` | 4 | 정상 최대 부하는 VU 4 |
| `EMBEDDING_BATCH_SIZE` | 4 | 주 batch size 4 |
| Worker timeout | 180초 | client timeout 180초 |
| Worker 503 재시도 | 최대 3회 | `worker-client` profile |
| VM `MAX_CONCURRENCY` | 1 | 요청이 겹치면 내부 대기 발생 가능 |
| video admission 상한 | 4 | VU 4와 5의 차이 확인 |
| admission 대기 한도 | 20초 | 긴 요청이 겹칠 때 503 가능 |
| 요청당 text 상한 | 32 | 32 초과는 성능 테스트에서 제외 |
| text 문자 상한 | 4,096자 | fixture 검증 시 확인 |
| payload 상한 | 262,144 bytes | 요청 생성 시 확인 |
| 모델 실제 처리 상한 | 512 token | 초과 부분은 오류 없이 잘림 |

스트레스 preset은 실행 직전에 Worker의 instance·동시성·batch size·timeout을 다시 읽는다. 현재 전제인 `1·4·4·180`과 다르면 실행을 막고 VU 조건을 다시 계산한다.

## 4. 입력 데이터

DB 원문과 사용자·project·video 식별자는 fixture나 결과에 저장하지 않는다. 저장하는 것은 통계와 합성 문장뿐이다.

### 4.1 통제된 길이 비교

길이 자체의 영향을 비교할 때는 각 구간을 균등하게 만든 정상 fixture를 사용한다.

| 구간 | raw token | 건수 |
|---|---:|---:|
| `short` | 64~128 | 200 |
| `medium` | 129~256 | 200 |
| `long` | 257~384 | 200 |
| `xlong` | 385~480 | 200 |
| `boundary` | 481~512 | 200 |

512 token 초과 입력은 모델에서 모두 512로 잘리므로 길이 비교에 섞지 않는다. 잘림 확인용 fixture는 별도로 둔다.

| 구간 | raw token | 건수 |
|---|---:|---:|
| `over_limit` | 513~768 | 50 |
| `observed_tail` | 769~896 | 50 |

### 4.2 현재 운영 입력을 대표하는 스트레스 부하

스트레스 테스트는 실제 DB 133행의 token 구간 비율에 맞춘 합성 `observed-mix`를 사용한다.

| 구간 | 건수 |
|---|---:|
| short | 2 |
| medium | 5 |
| long | 14 |
| xlong | 30 |
| boundary | 3 |
| over_limit | 57 |
| observed_tail | 22 |

실제 DB 전체 133행 중 79행(59.4%)이 512 token을 넘었다. 이 입력도 요청 크기와 tokenizer 비용을 반영하기 위해 스트레스 부하에는 포함하지만, 모델 계산 길이는 최대 512 token이다.

세부 통계와 fixture 동일성 지문은 다음 파일이 기준이다.

- `load-tests/k6/data/batch-embedding-db-profile.json`
- `load-tests/k6/data/batch-embedding-enriched-texts.manifest.json`

## 5. 완료된 기준 측정

### 5.1 Smoke

| 조건 | 결과 |
|---|---|
| short·batch 1·VU 1·10초 | 10/10 성공, p95 1.71초 |
| 513~768 token·batch 1·VU 1·10초 | 2/2 성공, 평균 5.41초 |

두 run 모두 응답 배열 검증, VM 회복 판정, OOM·restart·외부 부하 없음 조건을 통과했다. 경계 계약은 별도 live probe에서 빈 문자열 400, 4,097자 400, text 33개 400, payload 초과 413을 확인했다.

### 5.2 길이별 결과

공통 조건은 batch size 4, VU 1, 2분이다.

| 구간 | token p50 | texts/s | batch p50 | batch p95 |
|---|---:|---:|---:|---:|
| short | 96 | 1.104 | 3.51초 | 4.31초 |
| medium | 192.5 | 0.654 | 5.96초 | 6.95초 |
| long | 320.5 | 0.360 | 10.97초 | 11.76초 |
| xlong | 432.5 | 0.251 | 15.87초 | 16.18초 |
| boundary | 496.5 | 0.202 | 19.80초 | 19.86초 |

확인된 내용:

- token이 길어질수록 처리시간은 늘고 texts/s는 감소했다.
- boundary batch p50은 short의 5.64배이며 texts/s는 18.3% 수준이다.
- CPU 최대치는 약 58%로 비슷했다. 차이는 자원 고갈보다 긴 입력의 추론시간에서 발생했다.
- boundary 처리시간 19.8초가 admission 대기 한도 20초와 거의 같으므로, VU 4에서는 뒤 요청이 503으로 끝날 가능성이 있다.

## 6. 다음 실행: VM 스트레스 테스트

### 6.1 왜 VU 4인가

현재 Worker 한 instance에는 consumer가 4개 있다. 각 consumer는 임베딩 응답을 받은 뒤 다음 batch를 보낸다.

따라서 대량 영상이 업로드되어도 VM에 영상 수만큼 요청이 동시에 들어오는 것이 아니라, 최대 4개의 요청 흐름이 오래 유지된다. 이를 `constant-vus=4`로 재현한다.

- VU: 동시에 진행되는 Worker 요청 흐름 수
- batch size: HTTP 요청 하나에 담는 text 수
- `VU 4 / batch 4`: 동시에 요청 최대 4개, text 최대 16개 처리 중
- VU는 RPS가 아니다. 응답이 느려지면 실제 RPS도 낮아진다.

### 6.2 재시도 profile

| profile | 503 처리 | 목적 |
|---|---|---|
| `raw` | 재시도 없음 | VM이 처음 반환한 결과 확인 |
| `worker-client` | 최대 3회 지수 backoff와 jitter 후 재시도 | 현재 Worker client 정책 재현 |

timeout은 재시도하지 않는다. 같은 batch를 재시도할 때 trace ID는 유지하며, 최초 요청과 재시도 요청은 별도 집계한다.

### 6.3 실행 행렬

| preset | VU | batch | 입력 | profile | 시간 | 목적 |
|---|---:|---:|---|---|---:|---|
| S1 | 1 | 4 | observed-mix | raw | 2분 | 대표 입력 단일 흐름 기준선 |
| S2 | 4 | 4 | observed-mix | raw | 10분 | 현재 Worker 최대 정상 부하 |
| S3 | 4 | 4 | observed-mix | worker-client | 30분 | 재시도를 포함한 지속 부하 |
| S4 | 5 | 4 | observed-mix | worker-client | 5분 | 현재 구조를 넘긴 안전 여유 |

S4는 현재 운영 트래픽을 재현하는 run이 아니다. Worker scale-out 또는 설정 오류로 다섯 번째 흐름이 생겼을 때의 경계를 확인한다.

### 6.4 진행 조건

1. S1으로 입력·응답·계측이 정상인지 확인한다.
2. S2에서 timeout·503·OOM·restart·회복 실패가 없을 때만 S3로 진행한다.
3. S3에서 0~5분, 12~17분, 25~30분 지표를 비교한다.
4. S4는 S3까지 증거를 수집한 뒤 별도 경계 결과로 실행한다.

S1~S3에서는 첫 503도 안정성 실패다. S4에서는 503을 경계 관찰값으로 허용하지만, 재시도 소진·timeout·응답 오류는 실패다.

### 6.5 측정값

- 성공 texts/s와 batch p50·p95·max
- 최초 요청 수와 재시도 요청 수
- 최초 503, 재시도 성공, 재시도 소진, 증폭률
- admission queue wait·depth와 inference 시간
- 첫·중간·마지막 5분의 texts/s·p95·503
- VM CPU·memory·network, OOM·container/VM restart
- 종료 후 1분 안의 queue drain·health·단건 probe

응답 검증은 각 VU의 첫 성공과 이후 50번째 성공마다 수행한다. 진행 중인 요청을 종료하면서 잘못 끊지 않도록 `gracefulStop`은 4분으로 둔다.

## 7. 후속 탐색

스트레스 기준선을 확보한 뒤 한 변수씩 바꿔 개선 후보를 찾는다.

### 7.1 Batch size

VU 1과 balanced 입력을 고정하고 batch size `1 → 4 → 8 → 16`을 비교한다.

확인할 값은 texts/s, p95, payload, memory다. 처리량이 늘더라도 p95·payload 오류·memory가 급증하면 그 직전 값을 후보로 삼는다.

### 7.2 동시성

batch size 4와 balanced 입력을 고정하고 VU `1 → 2 → 4 → 5`를 비교한다.

확인할 값은 texts/s, queue wait, 503, timeout이다. VU를 늘려도 texts/s가 늘지 않고 queue wait만 증가하면 VM 내부 동시 처리 한계로 본다.

### 7.3 반복

탐색에서 확인된 안정점과 첫 실패점만 같은 조건으로 총 3회 측정한다. 모든 조합을 기계적으로 반복하지 않는다.

## 8. 실행 명령

세션 시작:

```bash
scripts/load-test/runner.sh batch-embedding-start \
  --model-version bge-m3-base
```

스트레스 preset:

```bash
scripts/load-test/runner.sh batch-embedding-stress-run --preset S1
scripts/load-test/runner.sh batch-embedding-stress-run --preset S2
scripts/load-test/runner.sh batch-embedding-stress-run --preset S3
scripts/load-test/runner.sh batch-embedding-stress-run --preset S4
```

일반 비교 run 예시:

```bash
scripts/load-test/runner.sh batch-embedding-run \
  --scenario capacity \
  --input-set capacity \
  --input-bucket balanced \
  --batch-size 4 \
  --vus 1 \
  --duration 2m
```

세션 종료와 VM 상태 복원:

```bash
scripts/load-test/runner.sh batch-embedding-stop
```

결과 경로:

```text
artifacts/load-tests/<run-id>/batch-embedding-capacity/
```

## 9. 중단·무효 조건

다음 상황에서는 장시간 run으로 진행하지 않고 결과와 로그부터 수집한다.

- 첫 client timeout 또는 503 재시도 소진
- OOM, container/VM restart, model reload, health 실패
- runner CPU·network 포화 또는 dropped iteration
- `video_preprocess` 이외의 요청이 같은 VM에 유입
- 실행 중 model·VM·Worker 설정 또는 fixture hash 변경

다음 run은 비교 결과에서 제외하고 다시 실행한다.

- target sampler 또는 5분 구간 지표 누락
- 실행 시작 시 이전 admission queue가 남아 있음
- 응답 개수·차원·NaN·Inf 검증 실패
- `observed-mix`·manifest hash 불일치
- 종료 후 1분 안에 endpoint가 회복되지 않음

## 10. 완료 기준

- [x] DB 원문 없이 현재 입력 길이와 형태를 재현한다.
- [x] 정상 용량·잘림·경계 fixture가 분리돼 있다.
- [x] 길이에 따른 추론시간과 texts/s 차이를 설명할 수 있다.
- [x] S1~S4를 같은 조건으로 재현할 실행기가 준비돼 있다.
- [ ] S2에서 현재 Worker 최대 부하의 안정 여부를 판정한다.
- [ ] S3에서 재시도 증폭과 장시간 성능 저하 여부를 판정한다.
- [ ] S4에서 scale-out 안전 여유를 확인한다.
- [ ] batch size와 동시성 개선 후보를 수치로 비교한다.
- [ ] 과부하 종료 뒤 VM 회복 여부를 확인한다.
- [ ] 다음 개선안을 최소 한 가지 수치로 뒷받침한다.
