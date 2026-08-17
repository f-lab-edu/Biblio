# 영상 파이프라인 개선 후보

- 작성일: 2026-08-16
- 대상: `services/pipeline-worker`, `services/managed-embedding-endpoint`, `infra/terraform/envs/gcp-perf`
- 전제: 2026-08-13 처리능력 테스트 계획(`docs/계획 일정/2026-08-13-영상-파이프라인-처리능력-테스트-계획.md`)의 기준선은 아직 없다. 아래는 코드와 배포 설정만으로 확인되는 구조적 상한과 개선 후보다.

## 요약

후보를 성격별로 셋으로 나눈다. 확실성이 다르므로 섞어서 다루지 않는다.

**A. 자원을 더 넣지 않고 고칠 수 있는 것.** 코드 구조에서 오는 대기 구간과 낭비다. CPU나 메모리를 얼마로 잡든 존재한다. 2절과 3절.

**B. 실패를 만드는 구성.** 느려지는 게 아니라 영상이 최종 실패한다. 배치 임베딩 VM의 슬롯 2개·요청 한도 4·대기 20초 조합(1.3)과 `/tmp`가 메모리 파일시스템인 점(1.2)이 여기 속한다.

**C. 측정으로 판정할 것.** worker vCPU 1개에 동시성 4(1.1)가 여기다. 파이프라인의 상당 부분이 STT·Vision·임베딩 응답을 기다리는 시간이라, CPU 경합이 실제로 전체 처리시간을 지배하는지는 기준선을 봐야 안다.

A와 B를 먼저 처리하고, C는 계획 5.2의 판정표로 가른 뒤에 손댄다.

---

## 1. 구성에서 오는 상한

### 1.1 worker vCPU 1개에 동시성 4 — 측정으로 판정할 것

사실:

- `infra/terraform/modules/cloud_run_worker/variables.tf:64` — `cpu` 기본값 `"1"`
- `infra/terraform/envs/gcp-perf/main.tf:501` — pipeline-worker가 `cpu`를 넘기지 않아 기본값 1을 쓴다. `memory`만 `"4Gi"`로 덮는다.
- `infra/terraform/envs/gcp-perf/main.tf:522` — `WORKER_CONCURRENCY = "4"`
- `services/pipeline-worker/src/bootstrap.py:272` — `worker_concurrency` 개수만큼 consumer 코루틴을 한 프로세스, 한 이벤트 루프에서 돌린다.

CPU를 실제로 쓰는 구간은 ffmpeg뿐이다. 오디오 추출(`extract_audio`), STT용 오디오 분할(`extract_audio_part`), 청크별 키프레임 추출(`extract_keyframe`)이 `asyncio.to_thread`로 subprocess를 띄운다. 스레드로 빼도 vCPU는 하나라 4건이 겹치면 나눠 쓴다.

**다만 이것이 처리량을 지배한다고 단정할 수 없다.** 단계별 성격이 이렇게 갈린다.

| 단계 | 성격 |
|---|---|
| download | GCS 네트워크 I/O — 대기 |
| audio | ffmpeg FLAC 인코딩 — CPU |
| stt | chirp_3 BatchRecognize, 원격 비동기 작업 — 대기 |
| chunk_enrichment | 키프레임 ffmpeg는 CPU, Vision 호출은 대기 |
| embedding | 임베딩 VM HTTP — 대기 |
| persist | DB — 대기 |

15분 영상 하나에서 CPU 실작업은 FLAC 인코딩 1회와 키프레임 10~15장 정도다. 반면 STT는 원격 처리 대기로 몇 분이 든다. 이 비율이면 vCPU 1개에 동시성 4는 대기 구간을 겹쳐 쓰는 정상적인 설계이고, CPU 경합은 ffmpeg가 겹치는 짧은 순간에만 생긴다. 위 시간 배분은 추정이며 기준선으로 확인해야 한다.

판정 조건: 아래 셋이 모두 성립할 때만 worker CPU가 병목이다.

1. `audio` + `chunk_enrichment` 합이 전체 처리시간에서 큰 비중을 차지한다.
2. 동시 실행 영상들의 ffmpeg 구간이 실제로 겹친다.
3. worker CPU가 계획 5.2 정의(80% 이상 10초 지속)의 포화에 든다.

계획 5.2 판정표의 "ffmpeg·chunk 보강 중 worker CPU 포화 → worker 연산 자원이 병목인 후보" 줄이 이 판정을 담당한다. 미리 결론 내지 않는다.

포화가 실제로 일어나면 2차 효과가 붙는다. 이벤트 루프가 굶어서 원격 호출의 await 처리까지 늦어지므로 단순 합산보다 나빠진다. 이것도 포화가 관측된 뒤의 이야기다.

개선안(판정 후): `cpu`를 `WORKER_CONCURRENCY`와 맞춘다(4). 설정 한 줄이라 되돌리기 쉽다. 다만 자원을 더 넣는 해법이지 설계를 고치는 것이 아니므로, 2절의 구조 개선을 먼저 적용한 뒤 남은 차이를 보고 결정한다.

비용: `cpu`만 올리고 `memory="4Gi"`를 유지하면 메모리 요금은 그대로라 시간당 단가는 4배가 아니라 3배쯤 된다. 모듈이 `cpu_idle = false`(항상 할당)라 인스턴스가 떠 있는 내내 과금되므로, 총비용은 `단가 × 켜둔 시간`이다. 처리가 빨라지면 상쇄된다. 실제 단가는 `scripts/gcp/billing/resource_cost.sql`로 확인한다.

### 1.2 `/tmp`가 메모리 파일시스템

사실:

- `services/pipeline-worker/src/utils/workdir.py:13` — `WorkdirManager`가 `tempfile.gettempdir()` 아래에 작업 디렉터리를 만든다. Cloud Run 컨테이너에서 이 경로는 `/tmp`다.
- Cloud Run의 `/tmp`는 볼륨을 따로 붙이지 않으면 메모리 파일시스템이고, 쓴 만큼 컨테이너 메모리 한도에서 차감된다.
- `pipeline_orchestrator.py:299` — 원본 영상을 `workdir / "source.bin"`으로 통째로 내려받는다.
- `settings.py:55` — 원본 크기 상한은 500MB다.

한 영상이 동시에 갖고 있는 것: 원본 영상, 추출한 FLAC, 긴 영상이면 STT part FLAC들, 청크 수만큼의 키프레임 JPEG. 30분 영상이면 원본 수백 MB에 FLAC이 50MB 안팎이다.

추정: 메모리 4Gi에 긴 영상 4건을 동시에 돌리면(S4) 메모리가 위험 구간에 들어간다. 계획 7절의 중단 조건 "worker 메모리 90% 이상 5분 지속"이 걸릴 수 있는 지점이다.

확인 방법: 테스트 중 Cloud Monitoring의 worker 메모리와 `timeline.csv`의 실행 중 영상 수·단계를 겹쳐 본다. 메모리가 다운로드 완료 시점마다 계단식으로 뛰면 이 원인이다.

개선안 세 가지 중 선택:

- Cloud Run 볼륨(디스크)을 붙이고 `WorkdirManager`의 base_dir를 그쪽으로 돌린다. 코드는 생성자 인자 하나다.
- 오디오 추출이 끝나면 원본을 즉시 지운다. 다만 키프레임 추출이 원본을 다시 쓰므로(`pipeline_orchestrator.py:494`) 순서를 바꾸거나 저해상도 proxy를 따로 만들어야 한다.
- 메모리를 올린다. 가장 단순하지만 비용이 늘고 원인은 그대로 남는다.

### 1.3 임베딩 VM의 슬롯·대기 설정

사실:

- `services/managed-embedding-endpoint/src/core/settings.py:43` — `max_concurrency` (동시 추론 슬롯)
- `infra/terraform/envs/gcp-perf/variables.tf:264` — `embedding_batch_max_concurrency` 기본값 **2**
- `variables.tf:277` — `embedding_batch_inference_threads` 기본값 **1**
- `variables.tf:146` — `embedding_video_preprocess_request_limit` 기본값 **4** (대기까지 허용하는 요청 수)
- `variables.tf:171` — `embedding_video_preprocess_wait_timeout_sec` 기본값 **20**
- `variables.tf:196` — `pipeline_embedding_batch_size` 기본값 **4**
- `variables.tf:290` — `embedding_batch_max_length` 기본값 **1024**
- VM은 `e2-standard-4` (`variables.tf:254`)

이 값들이 겹치면 이렇게 된다. worker 코루틴 4개가 각각 임베딩 요청을 하나씩 띄우면 요청 한도 4에 정확히 붙는다. 여유가 0이다. 그중 실제로 추론에 들어가는 건 2개고 나머지 2개는 슬롯을 기다린다. 20초 안에 슬롯을 못 잡으면 503이 나간다.

worker 쪽 반응(`infra/ai/embedding_client.py:277`): 503(`UNAVAILABLE`)만 재시도 대상이고 `max_retries=3`까지 지수 백오프로 재시도한다. 그래도 안 되면 예외가 위로 올라가고, `usecases/process_video.py:83`이 잡아서 영상을 바로 `FAILED`로 마킹한다. 큐로 되돌리지 않는다.

즉 **임베딩 슬롯 경합만으로 영상 전체가 최종 실패할 수 있다.** 앞 단계에서 이미 쓴 다운로드·STT·Vision 비용이 통째로 버려진다.

추정: bge-m3를 스레드 1개로 1024토큰까지 인코딩하면 텍스트 4개 배치가 수 초 단위다. 슬롯 2개에 요청 4개가 몰리면 뒤의 2개는 20초 안에 못 들어갈 가능성이 있다. S3(8건 동시)에서 확률이 더 올라간다.

확인 방법: `embedding.admission` 로그의 `admission_result`를 센다. `queue_timeout`이나 `queue_full`이 하나라도 나오면 이 경로다. `queue_wait_ms` 분포도 같이 본다.

개선안 (효과 큰 순서):

1. `pipeline_embedding_batch_size`를 4에서 16 정도로 올린다. 요청 수가 1/4로 줄어 슬롯 경합 자체가 줄어든다. 서버 상한은 `max_texts_per_request=32`라 여유가 있다. 다만 요청 하나가 길어지므로 `EMBEDDING_TIMEOUT_SEC`(현재 180)과의 관계를 같이 본다.
2. `embedding_video_preprocess_request_limit`을 worker 동시성보다 크게 잡아 여유를 만든다.
3. `embedding_video_preprocess_wait_timeout_sec`을 올린다. 배치 작업은 검색과 달리 20초 대기가 문제가 아니다. 검색 lane과 값이 분리돼 있으므로 검색 지연에 영향이 없다.
4. `embedding_batch_inference_threads`를 늘린다. vCPU 4개인데 스레드 1개면 남는다. 다만 슬롯 2개 × 스레드 N개가 vCPU를 넘지 않게 맞춰야 한다.

이 넷은 서로 영향을 주므로 한 번에 다 바꾸면 무엇이 효과였는지 못 가른다. 계획 6절의 "개선 하나씩" 원칙대로 1번부터 하나씩 한다.

### 1.4 임베딩 VM이 YouTube 프록시를 겸함

사실: `infra/terraform/envs/gcp-perf/main.tf:524` — `YOUTUBE_PROXY_URL = "socks5://${module.embedding_vm.private_ip}:1080"`. 배치 임베딩 VM에 WARP SOCKS5 프록시가 같이 떠 있다.

이번 테스트는 GCS에 미리 올린 고정 영상셋을 쓰므로 이 경로를 타지 않는다. 운영에서 외부 URL 업로드가 섞이면 다운로드 트래픽과 임베딩 추론이 같은 VM 자원을 나눠 쓴다. 기준선에는 안 잡히지만 개선 항목으로는 남겨둘 값이다.

---

## 2. 코드 구조에서 오는 대기 구간

### 2.1 고정 배치 gather (세 곳)

같은 패턴이 세 군데에 있다. 작업을 `동시성` 개수만큼 잘라서 `gather`로 묶고, 묶음 전체가 끝나야 다음 묶음을 시작한다. 한 작업이 느리면 이미 끝난 슬롯이 논다.

| 위치 | 코드 |
|---|---|
| 청크 Vision 보강 | `pipeline_orchestrator.py:534` — `for i in range(0, len(chunk_drafts), self._chunk_concurrency)` |
| STT part 전사 | `long_audio_transcription.py:220` — `for offset in range(0, len(parts), self._stt_concurrency)` |
| 임베딩 배치 | `pipeline_orchestrator.py:548` — 배치 하나씩 순차, 겹침 없음 |

청크 보강은 특히 낭비가 크다. `process_one` 안에 이미 `asyncio.Semaphore(self._chunk_concurrency)`가 있는데(`:485`) 바깥 루프가 또 배치로 묶고 있다. 세마포어만 남기고 바깥 루프를 없애면 동작은 같고 대기만 사라진다. Vision 호출 지연이 청크마다 다르므로(모델 응답 시간 편차) 효과가 있을 가능성이 높다.

개선안: 세 곳 모두 "전체 task 생성 + 세마포어" 구조로 바꾼다. 청크 보강은 코드를 지우는 쪽에 가깝다. STT part와 임베딩은 순서 보장이 필요하므로 결과를 index로 다시 정렬해야 한다(청크 보강은 `:538`에서 이미 정렬한다).

### 2.2 STT part 준비와 전사가 완전히 분리됨

사실: `long_audio_transcription.py:98` — `_prepare_parts`로 모든 part를 추출·업로드한 뒤 `_transcribe_parts`를 시작한다. `_prepare_parts` 내부도 part마다 추출 → 등록 → 업로드를 순차로 한다(`:171`).

part 0 업로드가 끝나면 part 1을 추출하는 동안 part 0 전사를 시작할 수 있다. 지금은 못 한다.

효과 추정: `AUDIO_PART_DURATION_SEC=900`(15분)이라 30분 영상은 part가 2~3개다. 절약되는 건 part 하나 추출·업로드 시간 한 번 정도다. 계획의 long fixture(25~30분)에서 STT 단계 시간의 몇 퍼센트인지는 `stage-events.jsonl`로 확인해야 판단할 수 있다. 우선순위는 낮다.

### 2.3 청크마다 DB 왕복 2회

사실: `pipeline_orchestrator.py:587` — `_assert_not_deleting`이 `touch_processing`과 `is_deleting` 두 번 DB를 친다. 이게 `process_one` 안에서 청크마다 호출된다(`:489`).

15분 영상의 청크가 10~15개면 왕복 20~30회, 여기에 `upsert_asset`(`:498`)이 청크당 1회 더 붙는다. Postgres는 VM 한 대에 세 역할을 겸하고 있어(메모리 기록의 의심 병목 항목) worker 4개가 동시에 이걸 하면 커넥션과 왕복 비용이 쌓인다.

개선안: 삭제 확인은 단계 경계에서만 하거나, `touch_processing`과 `is_deleting`을 한 쿼리(`UPDATE ... RETURNING status`)로 합친다. 후자가 호출 지점을 안 건드려서 영향 범위가 작다.

주의: 삭제 요청 반응 속도가 느려지는 트레이드오프가 있다. 한 쿼리로 합치는 쪽은 이 트레이드오프가 없다.

---

## 3. 단계별 비용 자체를 줄이는 후보

### 3.1 키프레임을 청크마다 따로 뽑는다

사실: `pipeline_orchestrator.py:493` — 청크마다 `extract_keyframe`를 호출하고, `ffmpeg_client.py:141`은 그때마다 ffmpeg 프로세스를 새로 띄워 원본을 다시 연다.

명령은 이렇다.

```
ffmpeg -ss {offset} -i {원본} -vf select='eq(pict_type,I)' -frames:v 1 -q:v 2 {출력}
```

두 가지가 걸린다.

- 청크 수만큼 프로세스 생성 + 컨테이너 파싱이 반복된다. 15분 영상 기준 10~15회로 추정한다(청크 크기 300어절 기준).
- `-ss`를 `-i` 앞에 둔 입력 seek는 원래 가장 가까운 키프레임으로 이동한다. 그 뒤에 다시 `select='eq(pict_type,I)'`로 I프레임을 기다리므로, 조건에 따라 필요 이상으로 디코딩할 수 있다. 필터를 빼도 결과는 사실상 같을 가능성이 높다.

개선안:

- 필터 제거를 먼저 시험한다. 한 줄이고 되돌리기 쉽다. 뽑힌 이미지가 달라지는지 fixture로 비교한다.
- 그다음 후보는 ffmpeg 한 번으로 전체 키프레임을 뽑는 것이다. 다만 청크 경계가 STT 결과에 따라 정해지므로 `-vf select` 표현식이 복잡해진다. 효과가 확인된 뒤에 한다.

측정 근거가 필요하다: 지금 `chunk_enrichment` 단계는 키프레임 추출, GCS 업로드, Gemini Vision 호출, 텍스트 정규화가 한 덩어리로 기록된다(`:224`). 어디가 느린지 갈리지 않는다. 아래 4절 참고.

### 3.2 키프레임 GCS 업로드가 청크 처리 안에 직렬로 들어있다

사실: `pipeline_orchestrator.py:497` — `process_one` 안에서 업로드를 `await`하고 그다음 Vision을 부른다. 업로드는 Vision 결과에 필요 없다(Vision은 로컬 경로를 쓴다, `:510`).

개선안: 업로드를 Vision 호출과 겹치게 하거나, 청크 처리 밖으로 빼서 나중에 일괄로 올린다. 다만 `upsert_asset`이 업로드 후 asset id를 만들어 `ChunkRecord`에 넣으므로(`:498`, `:523`) 순서를 바꾸려면 asset 등록 시점을 정리해야 한다. 영향 범위가 있어 우선순위는 중간이다.

---

## 4. 계획서 계측에 추가할 것

계획 3.1의 계측만으로는 위 후보 중 어느 것을 고를지 못 가른다. 두 가지가 빠져 있다.

**chunk_enrichment 내부 분해.** 현재 이 단계 시간에는 키프레임 추출, GCS 업로드, Vision 호출이 섞여 있다. 청크별로 `keyframe_ms`, `upload_ms`, `vision_ms`를 남겨야 3.1과 3.2 중 무엇을 할지 정할 수 있다. 청크 수도 같이 남긴다.

**embedding 단계의 대기와 추론 분리.** worker의 `embedding` 단계 시간에는 503 재시도 백오프가 포함된다. VM 쪽 `embedding.admission`의 `queue_wait_ms`·`inference_duration_ms`와 worker 쪽 재시도 횟수를 같은 `trace_id`로 붙여야 "느린 것"과 "거절당하고 기다린 것"이 갈린다. 재시도 횟수는 지금 로그에 안 남는다(`embedding_client.py:288`은 지연만 남긴다).

이 둘은 계획 3.1의 "추가 작업은 다음으로 제한한다" 목록에 3번 항목(단계 시작·종료 로그)의 연장으로 넣을 수 있다.

---

## 5. 실패·재시도 정책

### 5.1 자동 재시도가 없다

사실: `usecases/process_video.py:83` — 어떤 예외든 잡아서 `set_failed`로 종료한다. 큐 메시지는 `consumer.py:61`에서 정상 ack된다.

결과: STT나 Vision의 일시적 5xx, 임베딩 슬롯 경합 같은 회복 가능한 실패에서도 영상이 최종 실패한다. 30분 영상이면 이미 쓴 다운로드·오디오·STT 비용이 전부 버려진다.

부분 완화는 있다. `load_pipeline_state`가 오디오 asset과 transcript 존재를 확인해서(`pipeline_orchestrator.py:312`, `:419`) 재처리 시 그 두 단계는 건너뛴다. 하지만 재처리를 **누군가 다시 요청해야** 하고, `chunk_enrichment`에는 재개 지점이 없다. 청크 15개 중 14번에서 실패하면 키프레임 14개와 Vision 호출 14번을 다시 한다.

개선안 (범위 순):

1. 회복 가능한 실패는 ack하지 않고 큐 재전달에 맡긴다. `read_ct`가 이미 `BrokerMessage`에 보존되므로(#104 작업) 재전달 횟수 상한을 걸 수 있다. 상한 초과 시에만 `FAILED`로 확정한다.
2. 청크 결과를 청크 단위로 저장해 재개 지점을 만든다. `ChunkRecord`가 이미 `chunk_index`를 갖고 있어 자리는 있다.
3. 단계별 독립 큐로 분리한다. `docs/video-search-work-unit-and-straggler-design.md`가 다루는 범위다.

1번이 효과 대비 비용이 가장 낫다. 2번과 3번은 기준선에서 실패가 실제로 관측된 뒤에 판단한다.

### 5.2 visibility timeout과 긴 영상

사실:

- `settings.py:28` — `QUEUE_VISIBILITY_TIMEOUT_SEC` 기본 1800초(30분)
- `settings.py:38` — `STALE_PROCESSING_RECLAIM_SEC` 기본 1500초(25분)
- `bootstrap.py:66` — reclaim이 visibility보다 작아야 한다는 검증이 있다.

한 영상 처리가 25분을 넘기면 다른 코루틴이 `claim_processing`으로 가져갈 수 있고, 30분을 넘기면 메시지가 다시 보인다. long fixture(25~30분)를 4건 동시에 돌리는 S4에서, vCPU 1개 조건이면 실제 처리시간이 이 값에 닿을 수 있다.

확인 방법: 계획 5.1의 `read_ct`와 계획 7절의 "invisible message 재등장 여부"가 이미 이 항목을 잡는다. `read_ct > 1`이 나오면 여기다.

이건 개선 후보라기보다, 1.1(vCPU)을 고치면 같이 사라질 수 있는 증상이다. 순서상 vCPU를 먼저 본다.

---

## 6. 청킹 정책 (처리량과는 별개)

`docs/video-search-work-unit-and-straggler-design.md`가 이미 다룬 내용이지만, 코드 확인 결과 추가로 확정된 사실을 적는다.

- `chunking_service.py:70` — `_token_count`는 **공백으로 나눈 단어 수**를 센다. BGE 토크나이저 기준이 아니다. `CHUNK_MAX_TOKENS=300`은 어절 300개다.
- `embedding_batch_max_length=1024`로 잡아둔 것이 이 오차를 흡수하는 역할을 한다.
- 시간 상한이 없다. 말이 적은 구간에서는 청크 하나가 몇 분에 걸칠 수 있다.
- `chunking_service.py:59` — `_split_segment`가 문장 조각마다 부모 세그먼트의 `start_ms`/`end_ms`를 그대로 복사한다. 문장 단위 타임스탬프가 아니다.

검색 품질과 타임스탬프 정확도에 걸리는 항목이고, 처리량 기준선과는 직접 관련이 없다. 처리능력 개선을 끝낸 뒤에 별도로 다룬다.

---

## 7. 권하는 진행 순서

기준선이 나오기 전에는 순서를 확정하지 않는다. 다만 자원을 더 넣는 항목보다 설계 결함과 실패 원인을 앞에 둔다.

| 순서 | 항목 | 성격 | 종류 | 되돌리기 |
|---|---|---|---|---|
| 1 | 임베딩 `batch_size` 4 → 16 | B 실패 제거 | 설정 | 쉬움 |
| 2 | 청크 보강 고정 배치 제거 | A 구조 | 코드 (삭제) | 쉬움 |
| 3 | `/tmp` → 디스크 볼륨 | B 실패 제거 | 설정 + 인자 1개 | 쉬움 |
| 4 | 키프레임 `select` 필터 제거 | A 비용 | 코드 (한 줄) | 쉬움 |
| 5 | `_assert_not_deleting` 쿼리 합치기 | A 비용 | 코드 | 보통 |
| 6 | 회복 가능한 실패의 큐 재전달 | B 실패 제거 | 코드 | 보통 |
| — | worker `cpu` 1 → 4 | C 판정 대상 | 설정 | 쉬움 |
| — | 단계별 독립 큐 분리 | 설계 | 설계 | 어려움 |

각각 하나씩 적용하고 S1~S4를 다시 돌린다. 계획 6절의 "변경 목록 외 환경 값은 기준선과 일치" 조건을 지키려면 한 번에 하나만 바꿔야 한다.

worker `cpu`는 번호를 매기지 않는다. 1.1의 판정 조건 셋이 기준선에서 모두 성립할 때만 목록에 올린다. 성립하지 않으면 개선안이 아니라 기준선의 전제로 기록한다. 자원을 늘려 가리기 전에 1~6으로 병목이 어디로 옮겨가는지 먼저 보는 편이 낫다.

단계별 독립 큐 분리는 1~6 이후에 다시 판단한다.
