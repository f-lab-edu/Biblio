# [ADR-005] external-ai-adapter-provider-selection

* **상태 (Status):** Proposed
* **날짜 (Date):** 2026-03-10

## 1. 배경 및 문제 상황 (Context and Problem Statement)

* **상황:** Biblio는 `Search Service`에서 LLM 기반 답변 생성을 수행하고, `Pipeline Worker`에서 STT 기반 전처리를 수행한다. 이를 위해 `External AI Adapters` 계층에서 `LLMAdapter`, `STTAdapter` 추상 인터페이스와 운영 구현체(`GeminiLLMAdapter`, `GoogleSTTAdapter`)를 정의하고 있다.
* **문제:** 이때 "어떤 provider adapter를 쓸지"를 누가 결정할지 정해야 한다. `Search Service`와 `Pipeline Worker`가 각자 자신의 wiring 시점에 구체 adapter를 선택할 수도 있고, 별도의 adapter factory가 테넌트, 환경, 비용, 장애 상태를 보고 런타임에 선택할 수도 있다.
* **목표:** V1 범위에서 구현 복잡도를 과도하게 늘리지 않으면서도, 외부 SDK 의존성을 상위 서비스에서 분리하고 향후 확장 시 어떤 비용을 감수하는지 명확히 남긴다.

## 2. 고려한 옵션들 (Considered Options)

### 1. Caller가 구체 provider adapter를 직접 선택

`Search Service`는 `GeminiLLMAdapter`를, `Pipeline Worker`는 `GoogleSTTAdapter`를 각 컴포넌트의 wiring 단계에서 선택하고 주입받아 사용한다. 요청 단위로 provider를 바꾸지는 않는다.

**Pros**
* 구조가 가장 단순하고 이해하기 쉽다.
* V1에서 provider가 사실상 고정인 상황에 잘 맞는다.
* adapter 계층만으로도 외부 SDK 세부사항, 공통 오류 분류, timeout/retry/logging 정책을 서비스 코드 밖으로 밀어낼 수 있다.
* 디버깅 시 "어느 서비스가 어느 provider를 호출하는지"가 명확하다.
* factory나 라우팅 규칙을 위한 추가 설계, 설정, 테스트 비용이 들지 않는다.

**Cons**
* 상위 서비스가 provider 구현 이름을 직접 알아야 하므로 provider-neutral 하지는 않다.
* 향후 provider 교체 시 adapter 구현뿐 아니라 서비스 wiring도 함께 수정해야 한다.
* tenant별 정책, 비용 기반 선택, 장애 시 fallback 같은 요구가 생기면 선택 로직이 서비스 쪽으로 퍼질 수 있다.
* 서비스별 테스트/운영 전환 규칙이 분산될 가능성이 있다.

### 2. Adapter factory가 런타임에 provider를 선택

`Search Service`와 `Pipeline Worker`는 `LLMAdapter`, `STTAdapter` 추상 타입만 의존하고, 실제 구현체 선택은 factory가 테넌트, 환경, 비용 상태, 장애 상태를 보고 런타임에 결정한다.

**Pros**
* 상위 서비스가 provider 구현 이름을 몰라도 되므로 provider 추상화 수준이 높다.
* provider 교체, 다중 provider 지원, fallback, tenant별 정책 추가가 쉬워진다.
* 선택 규칙을 한 곳에 모아 중복과 분산을 줄일 수 있다.
* 테스트/로컬/운영 환경별 구현체 전환 지점을 중앙화할 수 있다.

**Cons**
* V1 기준으로는 구조와 설정이 과해질 수 있다.
* 라우팅 규칙, 장애 전환, 비용 정책이 들어가면 디버깅과 테스트가 복잡해진다.
* 실제 provider 선택이 호출 지점에서 보이지 않아 코드만 읽고 흐름을 이해하기 어려워질 수 있다.
* 현재 요구사항에 없는 미래 확장을 미리 구현하는 과설계가 될 수 있다.

## 3. 결정 사항 (Decision Outcome)

* **우리는 1번, 즉 caller가 구체 provider adapter를 직접 선택하는 방식을 선택한다.**
* **2번, 즉 adapter factory의 런타임 라우팅 방식은 향후 확장 옵션으로 남긴다.**
* **이유:**
  * 현재 V1 문맥에서 LLM은 `GeminiLLMAdapter`, STT는 `GoogleSTTAdapter`로 사실상 고정되어 있으며, runtime provider routing 요구가 없다.
  * 지금 단계에서 더 중요한 것은 provider 선택 유연성보다 구현 단순성, 디버깅 용이성, 빠른 전달이다.
  * adapter 계층만으로도 외부 SDK 세부 의존성, 공통 오류 계약, timeout/retry/logging 정책의 캡슐화라는 핵심 목적은 달성할 수 있다.
  * provider 선택 자체를 추상화하는 factory는 복수 provider, tenant별 정책, 비용 기반 선택, fallback 요구가 생길 때 도입해도 늦지 않다.
  * 특히 `Search Service`는 LLM timeout이 짧고 응답 SLA가 빡빡하므로, V1에서 runtime fallback이나 다중 provider probing을 넣으면 복잡도와 지연 위험이 커진다.

## 4. 결정된 설계 원칙 (Decision Details)

* `Search Service`와 `Pipeline Worker`는 각자의 구성(wiring) 시점에 운영 provider adapter를 선택하고 주입받는다.
* 요청 단위로 provider를 바꾸지 않으며, V1 범위에서는 tenant별 동적 선택, 비용 기반 라우팅, 장애 시 runtime fallback을 도입하지 않는다.
* 상위 서비스는 외부 SDK 세부 구현이 아니라 `LLMAdapter`, `STTAdapter` 계약에 의존하되, 어떤 운영 구현체를 바인딩할지는 각 컴포넌트 스펙과 구성 코드에서 명시한다.
* timeout, retry, circuit breaker 같은 호출 정책은 단일 provider 바인딩을 전제로 각 caller 스펙이 소유한다.
* 복수 provider 지원이나 routing 요구가 실제로 생기면, 이 결정을 재검토하고 별도 factory 또는 중앙화된 선택 계층을 도입한다.

## 5. 긍정적 효과 (Positive Consequences)

* V1 구현 범위를 단순하게 유지할 수 있다.
* 서비스별 호출 경로가 명확해 운영과 장애 분석이 쉽다.
* adapter 테스트와 서비스 테스트의 경계가 분명해진다.
* 현재 스펙에 이미 드러난 운영 구현체 문맥과 자연스럽게 맞는다.
* 요청 단위 provider 선택과 fallback을 배제함으로써 latency budget과 retry budget 해석이 단순해진다.

## 6. 부정적 효과 및 위험 요소 (Negative Consequences)

* 향후 provider 교체 시 service wiring과 관련 스펙까지 수정해야 한다.
  * **대응:** 서비스는 가능한 한 구체 SDK가 아니라 adapter 인터페이스와 최소한의 구성 코드에만 의존하도록 유지한다.
* provider 선택 로직이 상위 서비스에 노출된다.
  * **대응:** 선택 규칙이 2곳 이상에서 반복되기 시작하면 factory 또는 composition-root 수준의 중앙화로 승격한다.
* tenant별 정책, 비용 기반 선택, 장애 시 fallback 요구가 생기면 구조 변경이 필요하다.
  * **대응:** 해당 요구가 실제로 생기는 시점에 factory 도입을 재검토한다.
* provider-neutral 계약이 완전하지 않으므로 대체 provider가 `raw text`, `used_refs`, timeout/retry 계약을 동일하게 만족하지 못하면 관련 서비스 스펙까지 함께 조정해야 한다.
  * **대응:** provider 교체 전 adapter 호환성 체크리스트를 만들고, 계약 차이가 있으면 ADR을 갱신한다.

## 7. 결정 이후 후속 결과 (Consequences)


