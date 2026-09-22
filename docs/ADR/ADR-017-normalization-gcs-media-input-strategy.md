# [ADR-017] Normalization의 GCS 원본 영상 입력 방식

* **상태 (Status):** 승인됨(Accepted)
* **날짜 (Date):** 2026-08-23

## 1. 배경 및 문제 상황 (Context and Problem Statement)

* **상황:** `NORMALIZE_VIDEO`는 GCS 원본 영상에서 `15분 + 5초 overlap` FLAC part와 후보 JPEG를 만든다. 완료된 FLAC과 JPEG는 GCS에 업로드하고 로컬 임시파일은 삭제한다.
* **확정 제약:** 원본 영상 전체를 Worker 메모리나 로컬 디스크에 저장하지 않는다. 긴 영상 하나가 제한된 Cloud Run 메모리·임시 디스크를 오래 점유하지 않아야 한다.
* **문제:** GCS object를 iterator로 읽어 ffmpeg의 비탐색 `pipe:0`에 전달하면 파일 끝에 `moov`가 있는 일반 MP4를 열지 못할 수 있다. 또한 part마다 새 pipe를 열면 뒤쪽 part를 만들 때도 처음부터 해당 part의 끝 지점까지 읽어야 한다. part 수가 `n`이면 읽는 구간의 합은 대략 `1 + 2 + ... + n`으로 증가한다.
* **목표:** 원본 전체를 materialize하지 않으면서 일반 MP4를 안정적으로 seek하고, 실패한 part만 다시 실행할 수 있는 입력 방식을 선택한다.

## 2. 고려한 옵션들 (Considered Options)

### 1. 원본 전체를 로컬 임시파일로 다운로드

GCS 원본을 작업 디렉터리에 내려받고 같은 파일을 ffprobe, FLAC part, JPEG 생성에 재사용한다.

**Pros**
* 일반 MP4를 포함해 ffmpeg가 지원하는 seek 가능한 파일을 그대로 처리한다.
* GCS 원본 다운로드는 한 번이며 part마다 네트워크에서 앞부분을 다시 읽지 않는다.
* 구현과 로컬 재현이 단순하다.

**Cons**
* 원본 크기만큼 Worker 임시 디스크를 점유한다.
* 여러 normalization이 겹치면 디스크 사용량과 정리 실패 위험이 함께 증가한다.
* 원본 전체 로컬 저장 금지라는 확정 제약을 위반한다.

### 2. GCS iterator를 ffmpeg `pipe:0`에 전달

GCS object를 일정한 byte 크기로 읽어 ffprobe와 ffmpeg stdin에 순차 전달한다.

**Pros**
* 원본 전체를 메모리나 로컬 디스크에 저장하지 않는다.
* 메모리 사용량을 iterator chunk 크기로 제한할 수 있다.
* GCS SDK의 현재 인증 경계를 그대로 사용한다.

**Cons**
* stdin은 seek할 수 없어 파일 끝의 `moov`를 먼저 읽어야 하는 MP4가 실패할 수 있다.
* part별 실행은 목표 시점 이전 데이터를 매번 읽고 버리므로 긴 영상에서 전송량과 처리시간이 제곱에 가깝게 증가한다.
* `-ss`를 `-i` 앞에 두더라도 비탐색 pipe는 목표 byte 위치로 바로 이동할 수 없다.

### 3. 짧은 수명의 GCS signed URL을 ffmpeg HTTP 입력으로 전달

Worker가 normalization 실행 직전에 읽기 전용 signed URL을 만들고 ffprobe와 ffmpeg에 전달한다. ffmpeg는 HTTP Range 요청으로 MP4 metadata와 필요한 media byte 범위를 읽는다.

**Pros**
* 원본 전체를 메모리나 로컬 디스크에 저장하지 않는다.
* HTTP Range를 이용하므로 파일 끝의 `moov`와 필요한 part 시작 위치로 이동할 수 있다.
* `-ss`를 입력 앞에 둬 part 시작점까지의 순차 decode를 줄일 수 있다.
* part별 독립 실행과 완료 part 재사용 구조를 유지할 수 있다.

**Cons**
* Worker 서비스 계정에 signed URL 생성에 필요한 서명 권한이 필요하다.
* URL이 만료되거나 유출되면 처리 실패 또는 제한 시간 동안의 원본 노출이 발생한다.
* part마다 별도 ffmpeg를 실행하면 MP4 metadata와 일부 media range를 반복 요청할 수 있다.
* 실제 Range 동작과 전송량은 GCS·ffmpeg 통합 환경에서만 정확히 검증할 수 있다.

### 4. Worker 내부 range proxy 또는 seekable cache 구현

ffmpeg에는 로컬 HTTP URL을 제공하고, Worker가 GCS Range 요청과 제한된 byte cache를 직접 관리한다.

**Pros**
* signed URL을 ffmpeg 프로세스에 전달하지 않는다.
* cache 정책과 인증을 애플리케이션이 통제할 수 있다.
* 같은 byte 범위의 반복 요청을 줄일 수 있다.

**Cons**
* HTTP Range, 동시 요청, cache eviction, 취소, timeout을 직접 구현해야 한다.
* normalization 핵심 로직보다 media proxy 운영 코드가 더 복잡해질 수 있다.
* 잘못 구현하면 전체 파일 cache나 불완전한 Range 응답으로 돌아간다.

## 3. 결정 사항 (Decision Outcome)

* **옵션 3인 짧은 수명의 GCS signed URL과 ffmpeg HTTP Range 입력을 선택한다.**
* **옵션 1의 원본 전체 로컬 다운로드는 사용하지 않는다.** 장애 시 자동 fallback으로도 허용하지 않는다.
* **옵션 2의 `pipe:0`은 streamable 입력이 명확히 보장된 경우에만 사용할 수 있으며, 일반 업로드 영상의 기본 경로로 사용하지 않는다.**
* **옵션 4는 실측에서 Range 요청 중복이 병목으로 확인될 때 검토한다.**

**이유**
* 원본 전체 로컬 저장 금지와 일반 MP4 호환성을 동시에 만족한다.
* byte 위치 계산과 MP4 container 해석은 애플리케이션이 아니라 ffmpeg에 맡긴다.
* part별 완료·재시도 경계를 유지하면서 뒤쪽 part를 위해 원본 앞부분을 반복해서 순차 전송하는 문제를 줄인다.

## 4. 결정된 설계 원칙 (Decision Details)

### 4.1 입력과 seek

* signed URL은 normalization handler가 ffprobe 또는 ffmpeg를 호출하기 직전에 발급한다.
* ffmpeg에는 signed URL을 HTTP 입력으로 전달하고, part 추출에서는 `-ss`를 `-i` 앞에 둔다.
* HTTP 입력은 일시적 네트워크 오류에 재연결하도록 ffmpeg reconnect 옵션을 지정한다. 재연결로 복구되지 않는 오류만 part 실패로 올린다.
* 후보 JPEG는 같은 URL을 입력으로 한 번의 ffmpeg 실행에서 생성한다. 이 실행은 select filter가 전체 video stream을 판정해야 하므로 원본을 처음부터 끝까지 한 번 읽는다. Range로 줄어드는 전송량은 FLAC part 쪽에만 해당한다.
* GCS object 경로와 generation을 normalization 입력 identity에 포함한다. 재시도 중 둘 중 하나라도 달라지면 같은 작업으로 계속하지 않고 입력 변경 오류로 처리한다.
* 원본 object에 다운로드 시 자동 변환이 발생하는 `Content-Encoding: gzip`을 사용하지 않는다. Range가 무시되는 object 설정은 업로드 완료 검사에서 거부한다.

### 4.2 signed URL 보안

* URL은 읽기 전용 `GET`과 해당 object generation으로 제한한다.
* 만료 시간은 media operation timeout보다 길게 두고 고정된 안전 여유를 더한다.
* signed URL 전체를 DB, Queue payload, 구조화 로그, trace attribute, 예외 메시지에 저장하지 않는다.
* 로그에는 bucket을 제외한 작업 ID, GCS operation 종류, byte 수, 요청 횟수, duration, 상태만 기록한다.
* URL 생성 실패는 원본 전체 다운로드로 우회하지 않고 retryable normalization 실패로 처리한다.

### 4.3 part와 임시파일

* 각 미완료 part만 ffmpeg로 생성한다. 이미 DB에 확정된 연속 part는 재시도에서 건너뛴다.
* FLAC part는 결정적 GCS 경로에 업로드한 뒤 DB 상태와 후속 Queue 발행을 확정하고 로컬 파일을 삭제한다.
* 후보 JPEG도 업로드와 metadata 저장이 끝난 즉시 로컬 파일을 삭제한다.
* 성공, 실패, 취소와 관계없이 workdir 종료 시 남은 FLAC·JPEG를 정리한다.

### 4.4 관측과 검증

* 애플리케이션 stage 로그에는 signed URL 자체가 아닌 ffmpeg 실행시간과 part index를 기록한다.
  ffmpeg가 GCS에 직접 보내는 다운로드 byte와 HTTP 요청 횟수는 staging의 GCS 요청 지표로 검증한다.
* 다음 입력을 통합 테스트한다.
  * `moov`가 앞에 있는 MP4
  * `moov`가 끝에 있는 MP4
  * 15분보다 짧은 영상
  * 여러 part를 만드는 긴 영상
  * URL 만료와 서명 권한 오류
  * GCS generation 변경
  * part 생성 중 삭제 요청
* `pipe:0` 방식과 비교해 뒤쪽 part일수록 GCS 전송량이 늘어나지 않는지 확인한다.

## 5. 긍정적 효과 (Positive Consequences)

* Worker가 원본 크기만큼 메모리나 임시 디스크를 점유하지 않는다.
* 일반 MP4의 `moov` 위치와 관계없이 ffmpeg가 seek 가능한 입력을 받는다.
* 긴 영상의 뒤쪽 part를 만들 때 앞부분 전체를 반복해서 읽는 비용을 줄인다.
* 완료된 part를 재사용하고 실패한 part만 다시 처리하는 현재 작업 상태 모델을 유지한다.

## 6. 부정적 효과 및 위험 요소 (Negative Consequences)

* 서비스 계정의 URL 서명 권한이 추가된다.
  * **대응:** Worker 전용 서비스 계정에 필요한 최소 서명 권한과 GCS object read 권한만 부여하고 Terraform으로 관리한다.
* URL이 로그나 오류 메시지로 노출될 수 있다.
  * **대응:** media adapter가 URL을 포함한 command와 stderr를 그대로 기록하지 않으며, 테스트에서 query string 비노출을 확인한다.
* 처리 중 URL이 만료될 수 있다.
  * **대응:** operation timeout과 여유 시간을 기준으로 TTL을 계산하고, 만료 오류는 새 URL을 발급하는 작업 재시도로 복구한다.
* part별 ffmpeg 실행이 같은 metadata나 byte 범위를 반복해서 요청할 수 있다.
  * **대응:** GCS 전송 byte와 Range 요청 횟수를 계측한다. 병목으로 확인될 때만 단일 ffmpeg 다중 output 또는 제한된 range cache를 별도 ADR로 검토한다.
* GCS가 Range를 무시하면 전체 object가 전송될 수 있다.
  * **대응:** 자동 압축 해제가 필요한 object 설정을 금지하고 staging에서 응답 상태와 실제 전송량을 검증한다.
* signed URL 생성 방식은 런타임 자격 증명과 IAM 설정에 따라 달라질 수 있다.
  * **대응:** 배포 전에 실제 Worker 서비스 계정으로 URL 생성과 ffmpeg Range 읽기 smoke test를 통과시킨다.

## 7. 결정 이후 후속 결과 (Consequences)

* normalization 기본 입력은 generation에 고정된 V4 signed URL HTTP 입력으로 구현한다.
* 파이프라인 리팩토링 설계문서의 `GCS 원본 전달` 결정 기록은 이 ADR을 참조한다.
* Terraform은 Worker 서비스 계정의 URL 서명 권한과 GCS object 접근 권한을 관리한다.
* 로컬 자동 검증과 별개로, 배포 전 staging에서 실제 Worker 서비스 계정의 URL 생성과
  `moov` 위치가 다른 MP4의 HTTP Range 읽기 smoke test를 통과해야 한다.

## 참고

* [Cloud Storage object 다운로드와 Range 요청](https://docs.cloud.google.com/storage/docs/downloading-objects)
* [Cloud Storage signed URL](https://docs.cloud.google.com/storage/docs/access-control/signed-urls)
* [Cloud Storage V4 signed URL 생성](https://docs.cloud.google.com/storage/docs/access-control/signing-urls-with-helpers)
* [FFmpeg HTTP 프로토콜과 seekable 설정](https://ffmpeg.org/ffmpeg-protocols.html)
* [영상 파이프라인 작업 단위 리팩토링 설계](../계획%20파일들/2026-08-17-영상-파이프라인-작업단위-리팩토링-설계.md)
