# 테스트 결과 분석

부하 테스트 산출물을 다시 계산해 만든 분석 문서를 모아 둔다.
원본 실행 산출물(`artifacts/load-tests/`)은 `.gitignore` 대상이라 사라질 수 있으므로,
**보고서와 그 근거가 되는 집계 JSON은 이 폴더에 함께 둔다.**

## 목록

### 영상 파이프라인 S3·S4 구버전 vs 리팩토링 (2026-08-26)

| 파일 | 내용 |
|---|---|
| `video-pipeline-s3-s4-performance-comparison.md` | 본 보고서. 처리량·완료 지연·Queue 대기·stage·자원·신뢰성 비교와 병목 판정 |
| `video-pipeline-s3-s4-metrics.json` | 보고서의 모든 수치가 나온 기계 판독 집계 결과 |

- 계산 스크립트: `scripts/load-test/analyze_video_pipeline_comparison.py`
  (다른 부하 테스트 도구와 함께 두었다. 원본 산출물을 읽기만 하고 수정하지 않는다.)
- 재현: `python3 scripts/load-test/analyze_video_pipeline_comparison.py`
- 분석 대상 실행 4개 (원본 위치 `artifacts/load-tests/video-pipeline/`):
  - S3 구버전 `20260816T122033Z-video-s3`
  - S3 리팩토링 `20260825T183422Z-video-s3-refactored-norm2-embed2-maxlen1024-wait300-worker450`
  - S4 구버전 `20260825T1812KST-video-s4-baseline-f572829-sttfix-ffmpegfix-gcs300-maxlen1024`
  - S4 리팩토링 `20260826T005351Z-video-s4-refactored-assembly-retry-norm2-embed2-maxlen1024-wait300-worker450`
- 작성 요청 프롬프트: `docs/prompts/video-pipeline-s3-s4-performance-comparison-report.md`

핵심 결론 세 줄:

1. S4 선두 작업 막힘 해소. 구버전은 3회차 모두 long 4개가 다 끝난 뒤 short가 완료됐고, 리팩토링은 short 12/12가 마지막 long보다 먼저 끝났다.
2. embedding endpoint 입장 대기와 503은 사라졌지만 endpoint 처리 능력 자체는 그대로다(batch 4 추론 86.2 → 86.8초). 대기 위치가 옮겨졌다.
3. 현재 1순위 병목은 `EMBED_BATCH` 실행 슬롯 2개다. S4 완료 지연의 54~58%가 이 구간이다.

## 이 폴더에 문서를 추가할 때

- 보고서와 집계 JSON을 같은 이름 앞머리로 짝지어 둔다.
- 계산 스크립트는 `scripts/` 아래에 두고 보고서에서 경로로 가리킨다.
- 어떤 실행 산출물을 썼는지 run ID를 보고서와 이 목록 양쪽에 적는다.
