# 영상 검색 서비스의 작업 단위와 긴 작업 방지 설계

보통은 영상을 하나의 고정된 시간 단위로 잘라 모든 단계에 공통 적용하지 않습니다.

핵심은 두 단위를 분리하는 것입니다.

- 검색 단위: 벡터 하나가 영상의 어느 정도 내용을 나타낼지
- 작업 단위: worker가 한 번 가져가 처리하고, 실패하면 다시 실행할 범위

예를 들어 STT 작업은 10분 단위로 실행하더라도, 그 결과는 30초 안팎 또는 200토큰 안팎의 검색 청크 여러 개로 만들 수 있습니다.

## 단계별로 다른 단위를 사용함

| 처리 단계 | 비용을 결정하는 요소 | 권장 시작점 |
|---|---|---|
| 다운로드 | 파일 크기·네트워크 | 영상 파일 단위 |
| 오디오 변환 | 영상 길이·codec | 전체 영상 또는 10~15분 조각 |
| STT | 오디오 재생시간 | 5~15분, 2~5초 겹침 |
| 장면 분석 | 장면 수·추출 프레임 수·해상도 | shot 경계 기반, 긴 shot에는 시간 상한 |
| 검색 청크 | 말의 의미·BGE 토큰 수 | 문장 경계 + 128~224토큰 + 시간 상한 |
| 임베딩 요청 | batch 전체 토큰 수 | 고정 개수보다 총 토큰 예산 |
| 벡터 저장 | 청크 개수 | 청크 또는 작은 묶음 단위 |

위 숫자는 업계의 강제 표준이 아니라 시작점입니다. 실제 적정값은 해당 모델과 VM에서 측정해 정합니다.

시각 정보는 고정된 30초 간격만 사용하기보다 장면 전환을 기준으로 나누는 방식이 자연스럽습니다. Google Video Intelligence와 AWS Rekognition도 shot의 시작·끝 시각을 반환하는 구조입니다. [Google Shot Change Detection](https://cloud.google.com/video-intelligence/docs/feature-shot-change), [Amazon Rekognition Video Segments](https://docs.aws.amazon.com/rekognition/latest/dg/segments.html)

## 영상마다 다른 비용을 어떻게 맞추나

같은 10분 영상이라고 비용이 같지는 않습니다.

- 4K 60fps H.265 영상은 decode 비용이 큼
- 720p 30fps H.264 영상은 상대적으로 가벼움
- 말이 빠른 영상은 같은 시간에도 텍스트 토큰이 많음
- 장면 전환이 많은 영상은 처리할 keyframe이 많음
- OCR과 caption이 길면 임베딩 입력도 길어짐

그래서 실제로는 “시간이 같은 작업”보다 “예상 비용이 비슷한 작업”을 만듭니다.

```text
STT 비용       ≈ 오디오 재생시간
Vision 비용    ≈ 추출 프레임 수 × 프레임 해상도
Embedding 비용 ≈ batch에 들어간 토큰 수와 가장 긴 입력 길이
Download 비용  ≈ 파일 크기
```

그리고 원본 포맷 차이는 먼저 정규화합니다.

- STT용: 일정 sample rate·channel의 오디오
- Vision용: 일정 해상도의 keyframe 또는 proxy 영상
- Embedding용: 동일 tokenizer로 측정한 텍스트

이렇게 하면 원본이 4K인지, 어떤 영상 codec인지가 임베딩 단계까지 영향을 끌고 오지 않습니다.

## 긴 작업 하나가 전체를 막지 않게 하는 구조

핵심은 영상 하나를 하나의 거대한 queue 작업으로 만들지 않는 것입니다.

```text
영상 등록
   ↓
파일 정보 확인·정규화
   ↓
STT 조각 N개 생성 ──→ 독립 작업 queue ──→ 결과 합치기
   ↓
검색 청크 M개 생성 ─→ Vision 보강 작업 queue
   ↓
임베딩 작업 queue ──→ 길이가 비슷한 청크끼리 batch
   ↓
모든 조각 완료 확인
   ↓
영상 READY
```

이 구조에서는 1시간 영상 하나가 있어도 해당 영상의 여러 조각이 queue에 들어갈 뿐입니다. 다른 짧은 영상의 조각도 중간에 처리될 수 있습니다.

Google Dataflow도 straggler 방지를 위해 큰 작업을 나누고, 느린 worker에 남은 일을 다른 worker로 재분배합니다. 다만 하나의 record 자체가 너무 크면 더 이상 나눌 수 없으므로 처음부터 작업 하나에 상한을 둬야 합니다. [Dataflow 동적 작업 재분배](https://docs.cloud.google.com/dataflow/docs/dynamic-work-rebalancing)

구체적으로 다음 장치가 필요합니다.

- 조각별 독립 queue message
- `video_id + stage + part_index + version` 멱등성 key
- 조각별 결과 저장과 checkpoint
- 실패하면 영상 전체가 아니라 해당 조각만 재시도
- worker lease·heartbeat·timeout
- 재시도 한도 초과 시 DLQ
- 영상 하나가 worker를 독점하지 않도록 영상별 동시 실행 상한
- 짧은 영상과 긴 영상이 섞이도록 공정한 scheduling
- 모든 조각이 완료됐을 때만 부모 영상을 `READY`로 바꾸는 fan-in

긴 오디오는 비동기 STT 작업으로 처리하는 것이 일반적이며, Google STT도 1분이 넘는 오디오에는 비동기 Batch Recognition을 사용합니다. [Google Cloud 장시간 오디오 처리](https://cloud.google.com/speech-to-text/docs/batch-recognize)

## 현재 Biblio 상태

현재도 일부는 이미 이 방식입니다.

- 긴 오디오: 기본 15분 단위
- 오디오 overlap: 5초
- STT 조각 동시 처리: 2개
- 청크 Vision 보강: 2개씩 병렬
- 영상 간 동시 처리: worker 4개

하지만 큰 경계에서는 아직 영상 하나가 하나의 긴 작업입니다.

`ProcessVideoUseCase → PipelineOrchestrator.run`이 다음 단계를 모두 순서대로 실행합니다.

```text
다운로드 → 오디오 → STT → 청크 보강 → 임베딩 → 저장
```

따라서 긴 영상 하나는 worker 슬롯 하나를 끝까지 점유합니다. 긴 영상 4개가 동시에 들어오면 worker 4개를 모두 점유하여 뒤의 짧은 영상도 기다립니다.

또한 내부에서도:

- Vision 보강은 2개를 `gather`하고 둘 다 끝나야 다음 2개 시작
- 임베딩 batch는 영상 안에서 순차 실행

이 구조에서는 두 작업 중 하나만 느려도 이미 끝난 슬롯이 다음 작업을 받지 못하는 구간이 생길 수 있습니다.

## Biblio에 적합한 현실적인 단위

현재 규모에서는 바로 전체 파이프라인을 분산 stage queue로 뜯을 필요까지는 없습니다. 우선 다음 정도가 적절합니다.

### 검색 청크

다음 조건 중 하나에 먼저 도달하면 문장 경계에서 자릅니다.

- 최종 enriched text 목표: BGE 기준 192~224토큰
- hard max: 256토큰
- 시간 범위: 약 30~60초
- overlap: 문장 1개 또는 약 10%

시간과 토큰 제한을 함께 둬야 합니다.

```text
말이 빠른 영상 → 토큰 제한에 먼저 도달
말이 적은 영상 → 시간 제한에 먼저 도달
```

단, 현재처럼 transcript를 먼저 224토큰으로 자른 다음 caption·OCR·tag를 붙이면 최종 입력은 다시 256을 넘습니다. 따라서:

- transcript에 약 160~192토큰
- caption·OCR·tag에 약 32~64토큰

정도로 예산을 분리하거나, 보강한 다음 최종 BGE tokenizer 기준으로 다시 검사해야 합니다.

### STT

현재 `15분 + 5초 overlap + 동시성 2`는 시작점으로 충분히 합리적입니다. 당장 더 잘게 나눌 필요는 없습니다.

### Vision

고정 시간보다 shot 경계를 사용하되:

- 너무 짧은 shot은 인접 shot과 합치기
- 너무 긴 shot은 최대 시간으로 다시 나누기
- 각 작업에서 처리할 keyframe 수에 상한 두기

가 적절합니다.

### 임베딩

`batch size=4`만 보지 말고 총 토큰 수를 함께 제한하는 편이 좋습니다.

```text
짧은 청크 4개 → 한 batch
긴 청크 2~3개 → 한 batch
```

길이가 비슷한 청크끼리 묶으면 짧은 텍스트가 긴 텍스트의 padding 비용을 따라가는 문제도 줄일 수 있습니다.

## 제가 권하는 진행 순서

1. `max_length=256`은 설정으로 유지합니다.
2. 검색 청크를 `문장 경계 + BGE 토큰 + 최대 시간`으로 설계합니다.
3. 정확히 256을 채우지 말고 최종 enriched text를 192~224토큰으로 맞춥니다.
4. Vision의 2개 고정 묶음을 “전체 task 생성 + semaphore 2” 구조로 바꿔, 한 작업이 끝나면 바로 다음 작업이 시작되게 합니다.
5. 실제 영상 기준으로 단계별 p50/p95와 영상 전체 완료시간을 측정합니다.
6. 긴 영상이 실제로 짧은 영상의 대기시간을 밀어 올리는 것이 확인되면 STT·Vision·Embedding을 독립 queue 작업으로 분리합니다.

중요한 것은 파이프라인 전체를 `256`이라는 숫자에 영구적으로 묶지 않는 것입니다. `chunking_version`, tokenizer, 목표 토큰 수, hard max를 설정으로 분리해야 나중에 384·512·작은 모델로 바꾸더라도 원본 영상을 다시 가공하지 않고 재청킹·재임베딩할 수 있습니다.
