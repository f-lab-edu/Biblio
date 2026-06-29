# Frontend 구현 계획 — 1단계: 기반 + 인증

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Next.js 프론트엔드의 기반(스캐폴딩·테스트 환경·API 경계 계층)을 세우고, Mock 기반 이메일·비밀번호 가입/로그인이 동작하게 한다.

**Architecture:** 모든 외부 호출은 `src/lib/api` 한 곳을 지난다. 화면은 이 계층만 보고, 실제 백엔드냐 Mock이냐를 모른다. 아직 백엔드가 없는 인증은 Mock 구현으로 채우고, 환경 변수로 Mock/실제를 전환한다. 백엔드가 나오면 HTTP 구현만 채우고 전환 플래그를 끄면 된다.

**Tech Stack:** Next.js 15 (App Router) · TypeScript · Tailwind CSS · Vitest + React Testing Library + jsdom · npm

**작업 위치:** 프론트 워크트리 `/home/artyom9/project/Biblio-feat85-FE`. 코드는 그 안의 `frontend/` 디렉토리에 둔다(루트의 `services/`·`infra/`와 경로가 겹치지 않음). 아래 모든 경로는 `frontend/` 기준 상대 경로다.

---

## 전체 로드맵 (이 문서는 1단계만 상세화)

- **1단계 (이 문서):** 스캐폴딩 + 테스트 환경 + API 경계 계층 + 가입/로그인(Mock). 결과물 = 로그인되는 앱.
- **2단계 (별도 문서):** 프로젝트 목록 카드 그리드 + 새 프로젝트 생성(Mock).
- **3단계 (별도 문서):** 프로젝트 워크스페이스 — 영상 소스 패널, 업로드(파일/URL), 처리 상태 표시. 영상·업로드는 실제 백엔드 호출.
- **4단계 (별도 문서):** 대화형 검색 + 플로팅 미니 플레이어. 검색·재생은 실제 백엔드 호출.

설계 근거: `docs/Tech_Spec/Frontend/Frontend_Design.md`

---

## 파일 구조 (1단계에서 생성·수정)

| 경로 | 책임 |
|------|------|
| `frontend/` (전체) | create-next-app 스캐폴딩 결과 |
| `vitest.config.ts` | 테스트 러너 설정 (jsdom 환경) |
| `vitest.setup.ts` | 테스트 전역 설정 (RTL matcher, localStorage 정리) |
| `src/lib/api/types.ts` | 요청·응답 타입과 `Api` 인터페이스 |
| `src/lib/api/mock.ts` | 인메모리 Mock 구현 (가입/로그인) |
| `src/lib/api/http.ts` | 실제 HTTP 구현 (가입/로그인) |
| `src/lib/api/index.ts` | 환경 변수로 Mock/HTTP 선택 |
| `src/lib/auth/token.ts` | 인증 토큰 저장·조회·삭제 |
| `src/lib/auth/AuthContext.tsx` | 로그인 상태 전역 제공 |
| `src/app/login/page.tsx` | 로그인 화면 |
| `src/app/signup/page.tsx` | 가입 화면 |
| `src/app/page.tsx` | 홈 — 토큰 없으면 로그인으로 보냄 |
| `src/app/layout.tsx` | 루트 레이아웃에 AuthProvider 연결 |
| `.env.local` | `NEXT_PUBLIC_USE_MOCK`, `NEXT_PUBLIC_API_BASE_URL` |

---

## Task 0: Next.js 앱 스캐폴딩

**Files:**
- Create: `frontend/` (create-next-app 산출물 전체)

- [ ] **Step 1: 스캐폴딩 생성**

워크트리 루트에서 실행한다. 이미 git 저장소 안이라 create-next-app은 별도 git 초기화를 건너뛴다.

```bash
cd /home/artyom9/project/Biblio-feat85-FE
npx create-next-app@latest frontend \
  --typescript --tailwind --app --eslint \
  --src-dir --import-alias "@/*" --use-npm --yes
```

- [ ] **Step 2: 개발 서버가 뜨는지 확인**

Run:
```bash
cd /home/artyom9/project/Biblio-feat85-FE/frontend && npm run dev
```
Expected: `Local: http://localhost:3000` 출력. 브라우저에서 기본 페이지 확인 후 Ctrl+C.

- [ ] **Step 3: 커밋**

```bash
cd /home/artyom9/project/Biblio-feat85-FE
git add frontend
git commit -m "chore(fe): scaffold Next.js app"
```

---

## Task 1: 테스트 환경(Vitest + RTL) 구성

**Files:**
- Create: `frontend/vitest.config.ts`
- Create: `frontend/vitest.setup.ts`
- Create: `frontend/src/lib/__tests__/smoke.test.ts`
- Modify: `frontend/package.json` (test 스크립트)

- [ ] **Step 1: 의존성 설치**

```bash
cd /home/artyom9/project/Biblio-feat85-FE/frontend
npm install -D vitest @vitejs/plugin-react jsdom \
  @testing-library/react @testing-library/jest-dom @testing-library/user-event
```

- [ ] **Step 2: Vitest 설정 작성**

Create `frontend/vitest.config.ts`:
```ts
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "node:path";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./vitest.setup.ts"],
  },
  resolve: {
    alias: { "@": path.resolve(__dirname, "src") },
  },
});
```

Create `frontend/vitest.setup.ts`:
```ts
import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

afterEach(() => {
  cleanup();
  localStorage.clear();
});
```

- [ ] **Step 3: test 스크립트 추가**

`frontend/package.json`의 `"scripts"`에 추가:
```json
"test": "vitest run",
"test:watch": "vitest"
```

- [ ] **Step 4: 스모크 테스트 작성**

Create `frontend/src/lib/__tests__/smoke.test.ts`:
```ts
import { describe, it, expect } from "vitest";

describe("test harness", () => {
  it("runs", () => {
    expect(1 + 1).toBe(2);
  });
});
```

- [ ] **Step 5: 테스트 실행 확인**

Run: `npm test`
Expected: PASS 1개.

- [ ] **Step 6: 커밋**

```bash
cd /home/artyom9/project/Biblio-feat85-FE
git add frontend
git commit -m "test(fe): set up vitest + testing-library"
```

---

## Task 2: API 타입과 Mock 인증 구현

**Files:**
- Create: `frontend/src/lib/api/types.ts`
- Create: `frontend/src/lib/api/mock.ts`
- Test: `frontend/src/lib/api/__tests__/mock.test.ts`

- [ ] **Step 1: 실패하는 테스트 작성**

Create `frontend/src/lib/api/__tests__/mock.test.ts`:
```ts
import { describe, it, expect, beforeEach } from "vitest";
import { createMockApi } from "@/lib/api/mock";

describe("mock auth", () => {
  let api: ReturnType<typeof createMockApi>;
  beforeEach(() => {
    api = createMockApi();
  });

  it("signup returns a token and userId", async () => {
    const res = await api.signup({ email: "a@b.com", password: "pw12345" });
    expect(res.token).toBeTruthy();
    expect(res.userId).toBeTruthy();
  });

  it("login after signup returns a token", async () => {
    await api.signup({ email: "a@b.com", password: "pw12345" });
    const res = await api.login({ email: "a@b.com", password: "pw12345" });
    expect(res.token).toBeTruthy();
  });

  it("login with wrong password throws", async () => {
    await api.signup({ email: "a@b.com", password: "pw12345" });
    await expect(
      api.login({ email: "a@b.com", password: "wrong" })
    ).rejects.toThrow();
  });

  it("signup with an existing email throws", async () => {
    await api.signup({ email: "a@b.com", password: "pw12345" });
    await expect(
      api.signup({ email: "a@b.com", password: "pw12345" })
    ).rejects.toThrow();
  });
});
```

- [ ] **Step 2: 실패 확인**

Run: `npm test -- mock`
Expected: FAIL — `createMockApi` 없음.

- [ ] **Step 3: 타입 정의**

Create `frontend/src/lib/api/types.ts`:
```ts
export interface SignupRequest {
  email: string;
  password: string;
}

export interface LoginRequest {
  email: string;
  password: string;
}

export interface AuthResponse {
  token: string;
  userId: string;
}

export interface Api {
  signup(req: SignupRequest): Promise<AuthResponse>;
  login(req: LoginRequest): Promise<AuthResponse>;
}
```

- [ ] **Step 4: Mock 구현**

Create `frontend/src/lib/api/mock.ts`:
```ts
import type { Api, AuthResponse, LoginRequest, SignupRequest } from "./types";

interface MockUser {
  userId: string;
  email: string;
  password: string;
}

export function createMockApi(): Api {
  const users = new Map<string, MockUser>();

  function issueToken(userId: string): AuthResponse {
    return { token: `mock-token-${userId}`, userId };
  }

  return {
    async signup({ email, password }: SignupRequest): Promise<AuthResponse> {
      if (users.has(email)) {
        throw new Error("이미 가입된 이메일입니다.");
      }
      const userId = crypto.randomUUID();
      users.set(email, { userId, email, password });
      return issueToken(userId);
    },

    async login({ email, password }: LoginRequest): Promise<AuthResponse> {
      const user = users.get(email);
      if (!user || user.password !== password) {
        throw new Error("이메일 또는 비밀번호가 올바르지 않습니다.");
      }
      return issueToken(user.userId);
    },
  };
}
```

- [ ] **Step 5: 통과 확인**

Run: `npm test -- mock`
Expected: PASS 4개.

- [ ] **Step 6: 커밋**

```bash
cd /home/artyom9/project/Biblio-feat85-FE
git add frontend
git commit -m "feat(fe): add api types and mock auth"
```

---

## Task 3: 실제 HTTP 인증 구현

백엔드 발급 API가 아직 없으므로 호출 형태만 맞춰 둔다. `fetch`를 가로채 검증한다.

**Files:**
- Create: `frontend/src/lib/api/http.ts`
- Test: `frontend/src/lib/api/__tests__/http.test.ts`

- [ ] **Step 1: 실패하는 테스트 작성**

Create `frontend/src/lib/api/__tests__/http.test.ts`:
```ts
import { describe, it, expect, vi, afterEach } from "vitest";
import { createHttpApi } from "@/lib/api/http";

afterEach(() => vi.restoreAllMocks());

describe("http auth", () => {
  it("login POSTs to the auth endpoint and returns parsed body", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ token: "t", userId: "u" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    );
    vi.stubGlobal("fetch", fetchMock);

    const api = createHttpApi("https://api.test");
    const res = await api.login({ email: "a@b.com", password: "pw" });

    expect(res.token).toBe("t");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("https://api.test/api/v1/auth/login");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ email: "a@b.com", password: "pw" });
  });

  it("throws on non-2xx", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("nope", { status: 401 }))
    );
    const api = createHttpApi("https://api.test");
    await expect(
      api.login({ email: "a@b.com", password: "x" })
    ).rejects.toThrow();
  });
});
```

- [ ] **Step 2: 실패 확인**

Run: `npm test -- http`
Expected: FAIL — `createHttpApi` 없음.

- [ ] **Step 3: HTTP 구현**

Create `frontend/src/lib/api/http.ts`:
```ts
import type { Api, AuthResponse, LoginRequest, SignupRequest } from "./types";

export function createHttpApi(baseUrl: string): Api {
  async function post<T>(path: string, body: unknown): Promise<T> {
    const res = await fetch(`${baseUrl}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      throw new Error(`요청 실패 (${res.status})`);
    }
    return (await res.json()) as T;
  }

  return {
    signup(req: SignupRequest): Promise<AuthResponse> {
      return post<AuthResponse>("/api/v1/auth/signup", req);
    },
    login(req: LoginRequest): Promise<AuthResponse> {
      return post<AuthResponse>("/api/v1/auth/login", req);
    },
  };
}
```

- [ ] **Step 4: 통과 확인**

Run: `npm test -- http`
Expected: PASS 2개.

- [ ] **Step 5: 커밋**

```bash
cd /home/artyom9/project/Biblio-feat85-FE
git add frontend
git commit -m "feat(fe): add http auth client"
```

---

## Task 4: Mock/HTTP 선택기

**Files:**
- Create: `frontend/src/lib/api/index.ts`
- Create: `frontend/.env.local`
- Test: `frontend/src/lib/api/__tests__/index.test.ts`

- [ ] **Step 1: 실패하는 테스트 작성**

Create `frontend/src/lib/api/__tests__/index.test.ts`:
```ts
import { describe, it, expect } from "vitest";
import { resolveApi } from "@/lib/api";

describe("resolveApi", () => {
  it("returns a mock api when useMock is true", async () => {
    const api = resolveApi({ useMock: true, baseUrl: "" });
    const res = await api.signup({ email: "a@b.com", password: "pw12345" });
    expect(res.token).toContain("mock-token");
  });

  it("returns an http api when useMock is false", () => {
    const api = resolveApi({ useMock: false, baseUrl: "https://api.test" });
    expect(api.login).toBeTypeOf("function");
  });
});
```

- [ ] **Step 2: 실패 확인**

Run: `npm test -- api/__tests__/index`
Expected: FAIL — `resolveApi` 없음.

- [ ] **Step 3: 선택기 구현**

Create `frontend/src/lib/api/index.ts`:
```ts
import type { Api } from "./types";
import { createMockApi } from "./mock";
import { createHttpApi } from "./http";

export interface ApiConfig {
  useMock: boolean;
  baseUrl: string;
}

export function resolveApi(config: ApiConfig): Api {
  return config.useMock ? createMockApi() : createHttpApi(config.baseUrl);
}

export const api: Api = resolveApi({
  useMock: process.env.NEXT_PUBLIC_USE_MOCK !== "false",
  baseUrl: process.env.NEXT_PUBLIC_API_BASE_URL ?? "",
});

export type { Api } from "./types";
```

- [ ] **Step 4: 환경 변수 파일 작성**

Create `frontend/.env.local`:
```
NEXT_PUBLIC_USE_MOCK=true
NEXT_PUBLIC_API_BASE_URL=
```

- [ ] **Step 5: 통과 확인**

Run: `npm test -- api/__tests__/index`
Expected: PASS 2개.

- [ ] **Step 6: 커밋**

```bash
cd /home/artyom9/project/Biblio-feat85-FE
git add frontend
git commit -m "feat(fe): add api mock/http selector"
```

---

## Task 5: 토큰 저장소

**Files:**
- Create: `frontend/src/lib/auth/token.ts`
- Test: `frontend/src/lib/auth/__tests__/token.test.ts`

- [ ] **Step 1: 실패하는 테스트 작성**

Create `frontend/src/lib/auth/__tests__/token.test.ts`:
```ts
import { describe, it, expect } from "vitest";
import { getToken, setToken, clearToken } from "@/lib/auth/token";

describe("token store", () => {
  it("returns null when nothing is stored", () => {
    expect(getToken()).toBeNull();
  });

  it("stores and reads a token", () => {
    setToken("abc");
    expect(getToken()).toBe("abc");
  });

  it("clears a token", () => {
    setToken("abc");
    clearToken();
    expect(getToken()).toBeNull();
  });
});
```

- [ ] **Step 2: 실패 확인**

Run: `npm test -- token`
Expected: FAIL — 모듈 없음.

- [ ] **Step 3: 구현**

Create `frontend/src/lib/auth/token.ts`:
```ts
const TOKEN_KEY = "biblio.token";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  if (typeof window === "undefined") return;
  window.localStorage.removeItem(TOKEN_KEY);
}
```

- [ ] **Step 4: 통과 확인**

Run: `npm test -- token`
Expected: PASS 3개.

- [ ] **Step 5: 커밋**

```bash
cd /home/artyom9/project/Biblio-feat85-FE
git add frontend
git commit -m "feat(fe): add token storage"
```

---

## Task 6: 인증 컨텍스트

**Files:**
- Create: `frontend/src/lib/auth/AuthContext.tsx`
- Test: `frontend/src/lib/auth/__tests__/AuthContext.test.tsx`

- [ ] **Step 1: 실패하는 테스트 작성**

Create `frontend/src/lib/auth/__tests__/AuthContext.test.tsx`:
```tsx
import { describe, it, expect } from "vitest";
import { render, screen, act } from "@testing-library/react";
import { AuthProvider, useAuth } from "@/lib/auth/AuthContext";

function Probe() {
  const { token, signIn, signOut } = useAuth();
  return (
    <div>
      <span data-testid="token">{token ?? "none"}</span>
      <button onClick={() => signIn("t1")}>in</button>
      <button onClick={() => signOut()}>out</button>
    </div>
  );
}

describe("AuthContext", () => {
  it("starts signed out, signs in, signs out", () => {
    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>
    );
    expect(screen.getByTestId("token").textContent).toBe("none");

    act(() => screen.getByText("in").click());
    expect(screen.getByTestId("token").textContent).toBe("t1");

    act(() => screen.getByText("out").click());
    expect(screen.getByTestId("token").textContent).toBe("none");
  });
});
```

- [ ] **Step 2: 실패 확인**

Run: `npm test -- AuthContext`
Expected: FAIL — 모듈 없음.

- [ ] **Step 3: 구현**

Create `frontend/src/lib/auth/AuthContext.tsx`:
```tsx
"use client";

import { createContext, useContext, useEffect, useState } from "react";
import { getToken, setToken, clearToken } from "./token";

interface AuthState {
  token: string | null;
  ready: boolean;
  signIn: (token: string) => void;
  signOut: () => void;
}

const AuthCtx = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [token, setTokenState] = useState<string | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    setTokenState(getToken());
    setReady(true);
  }, []);

  function signIn(next: string) {
    setToken(next);
    setTokenState(next);
  }

  function signOut() {
    clearToken();
    setTokenState(null);
  }

  return (
    <AuthCtx.Provider value={{ token, ready, signIn, signOut }}>
      {children}
    </AuthCtx.Provider>
  );
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthCtx);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
```

- [ ] **Step 4: 통과 확인**

Run: `npm test -- AuthContext`
Expected: PASS 1개.

- [ ] **Step 5: 루트 레이아웃에 Provider 연결**

`frontend/src/app/layout.tsx`의 `<body>` 자식을 `AuthProvider`로 감싼다. 상단에 import 추가:
```tsx
import { AuthProvider } from "@/lib/auth/AuthContext";
```
`<body className={...}>` 안을 다음으로 바꾼다:
```tsx
<body className={...}>
  <AuthProvider>{children}</AuthProvider>
</body>
```

- [ ] **Step 6: 커밋**

```bash
cd /home/artyom9/project/Biblio-feat85-FE
git add frontend
git commit -m "feat(fe): add auth context and wire provider"
```

---

## Task 7: 로그인 화면

**Files:**
- Create: `frontend/src/app/login/page.tsx`
- Test: `frontend/src/app/login/__tests__/page.test.tsx`

- [ ] **Step 1: 실패하는 테스트 작성**

Create `frontend/src/app/login/__tests__/page.test.tsx`:
```tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

const login = vi.fn();
vi.mock("@/lib/api", () => ({ api: { login: (...a: unknown[]) => login(...a) } }));

import LoginPage from "@/app/login/page";
import { AuthProvider } from "@/lib/auth/AuthContext";

function renderPage() {
  return render(
    <AuthProvider>
      <LoginPage />
    </AuthProvider>
  );
}

describe("LoginPage", () => {
  beforeEach(() => {
    push.mockReset();
    login.mockReset();
  });

  it("submits email/password and redirects home on success", async () => {
    login.mockResolvedValue({ token: "t", userId: "u" });
    renderPage();

    await userEvent.type(screen.getByLabelText("이메일"), "a@b.com");
    await userEvent.type(screen.getByLabelText("비밀번호"), "pw12345");
    await userEvent.click(screen.getByRole("button", { name: "로그인" }));

    expect(login).toHaveBeenCalledWith({ email: "a@b.com", password: "pw12345" });
    expect(push).toHaveBeenCalledWith("/");
  });

  it("shows an error message on failure", async () => {
    login.mockRejectedValue(new Error("이메일 또는 비밀번호가 올바르지 않습니다."));
    renderPage();

    await userEvent.type(screen.getByLabelText("이메일"), "a@b.com");
    await userEvent.type(screen.getByLabelText("비밀번호"), "wrong");
    await userEvent.click(screen.getByRole("button", { name: "로그인" }));

    expect(
      await screen.findByText("이메일 또는 비밀번호가 올바르지 않습니다.")
    ).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: 실패 확인**

Run: `npm test -- login`
Expected: FAIL — 페이지 없음.

- [ ] **Step 3: 구현**

Create `frontend/src/app/login/page.tsx`:
```tsx
"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth/AuthContext";

export default function LoginPage() {
  const router = useRouter();
  const { signIn } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      const res = await api.login({ email, password });
      signIn(res.token);
      router.push("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "로그인에 실패했습니다.");
    }
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-sm flex-col justify-center gap-4 p-6">
      <h1 className="text-2xl font-bold">로그인</h1>
      <form onSubmit={onSubmit} className="flex flex-col gap-3">
        <label className="flex flex-col gap-1">
          이메일
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="rounded border p-2"
            required
          />
        </label>
        <label className="flex flex-col gap-1">
          비밀번호
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="rounded border p-2"
            required
          />
        </label>
        {error && <p className="text-sm text-red-600">{error}</p>}
        <button type="submit" className="rounded bg-black p-2 text-white">
          로그인
        </button>
      </form>
      <p className="text-sm">
        계정이 없나요? <Link href="/signup" className="underline">가입</Link>
      </p>
    </main>
  );
}
```

- [ ] **Step 4: 통과 확인**

Run: `npm test -- login`
Expected: PASS 2개.

- [ ] **Step 5: 커밋**

```bash
cd /home/artyom9/project/Biblio-feat85-FE
git add frontend
git commit -m "feat(fe): add login page"
```

---

## Task 8: 가입 화면

**Files:**
- Create: `frontend/src/app/signup/page.tsx`
- Test: `frontend/src/app/signup/__tests__/page.test.tsx`

- [ ] **Step 1: 실패하는 테스트 작성**

Create `frontend/src/app/signup/__tests__/page.test.tsx`:
```tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

const signup = vi.fn();
vi.mock("@/lib/api", () => ({ api: { signup: (...a: unknown[]) => signup(...a) } }));

import SignupPage from "@/app/signup/page";
import { AuthProvider } from "@/lib/auth/AuthContext";

function renderPage() {
  return render(
    <AuthProvider>
      <SignupPage />
    </AuthProvider>
  );
}

describe("SignupPage", () => {
  beforeEach(() => {
    push.mockReset();
    signup.mockReset();
  });

  it("submits and redirects home on success", async () => {
    signup.mockResolvedValue({ token: "t", userId: "u" });
    renderPage();

    await userEvent.type(screen.getByLabelText("이메일"), "a@b.com");
    await userEvent.type(screen.getByLabelText("비밀번호"), "pw12345");
    await userEvent.click(screen.getByRole("button", { name: "가입" }));

    expect(signup).toHaveBeenCalledWith({ email: "a@b.com", password: "pw12345" });
    expect(push).toHaveBeenCalledWith("/");
  });

  it("shows an error message on failure", async () => {
    signup.mockRejectedValue(new Error("이미 가입된 이메일입니다."));
    renderPage();

    await userEvent.type(screen.getByLabelText("이메일"), "a@b.com");
    await userEvent.type(screen.getByLabelText("비밀번호"), "pw12345");
    await userEvent.click(screen.getByRole("button", { name: "가입" }));

    expect(await screen.findByText("이미 가입된 이메일입니다.")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: 실패 확인**

Run: `npm test -- signup`
Expected: FAIL — 페이지 없음.

- [ ] **Step 3: 구현**

Create `frontend/src/app/signup/page.tsx`:
```tsx
"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth/AuthContext";

export default function SignupPage() {
  const router = useRouter();
  const { signIn } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      const res = await api.signup({ email, password });
      signIn(res.token);
      router.push("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "가입에 실패했습니다.");
    }
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-sm flex-col justify-center gap-4 p-6">
      <h1 className="text-2xl font-bold">가입</h1>
      <form onSubmit={onSubmit} className="flex flex-col gap-3">
        <label className="flex flex-col gap-1">
          이메일
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="rounded border p-2"
            required
          />
        </label>
        <label className="flex flex-col gap-1">
          비밀번호
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="rounded border p-2"
            required
          />
        </label>
        {error && <p className="text-sm text-red-600">{error}</p>}
        <button type="submit" className="rounded bg-black p-2 text-white">
          가입
        </button>
      </form>
      <p className="text-sm">
        이미 계정이 있나요? <Link href="/login" className="underline">로그인</Link>
      </p>
    </main>
  );
}
```

- [ ] **Step 4: 통과 확인**

Run: `npm test -- signup`
Expected: PASS 2개.

- [ ] **Step 5: 커밋**

```bash
cd /home/artyom9/project/Biblio-feat85-FE
git add frontend
git commit -m "feat(fe): add signup page"
```

---

## Task 9: 홈 라우트 보호

토큰이 없으면 로그인으로 보낸다. 토큰이 있으면 다음 단계(프로젝트 목록) 자리표시 화면을 보여 준다.

**Files:**
- Modify: `frontend/src/app/page.tsx` (create-next-app 기본 내용 전체 교체)
- Test: `frontend/src/app/__tests__/page.test.tsx`

- [ ] **Step 1: 실패하는 테스트 작성**

Create `frontend/src/app/__tests__/page.test.tsx`:
```tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";

const replace = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace }) }));

import Home from "@/app/page";
import { AuthProvider } from "@/lib/auth/AuthContext";
import { setToken } from "@/lib/auth/token";

function renderHome() {
  return render(
    <AuthProvider>
      <Home />
    </AuthProvider>
  );
}

describe("Home", () => {
  beforeEach(() => {
    replace.mockReset();
    localStorage.clear();
  });

  it("redirects to /login when there is no token", () => {
    renderHome();
    expect(replace).toHaveBeenCalledWith("/login");
  });

  it("shows the project list placeholder when signed in", () => {
    setToken("t1");
    renderHome();
    expect(replace).not.toHaveBeenCalled();
    expect(screen.getByText("내 프로젝트")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: 실패 확인**

Run: `npm test -- app/__tests__/page`
Expected: FAIL — 기본 페이지에는 해당 동작/문구 없음.

- [ ] **Step 3: 구현**

Replace `frontend/src/app/page.tsx` 전체:
```tsx
"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth/AuthContext";

export default function Home() {
  const router = useRouter();
  const { token, ready } = useAuth();

  useEffect(() => {
    if (ready && token === null) {
      router.replace("/login");
    }
  }, [ready, token, router]);

  if (!ready || token === null) return null;

  return (
    <main className="p-6">
      <h1 className="text-2xl font-bold">내 프로젝트</h1>
      <p className="mt-2 text-sm text-gray-500">
        프로젝트 목록은 다음 단계에서 구현합니다.
      </p>
    </main>
  );
}
```

참고: `AuthContext`는 마운트 후 `useEffect`에서 토큰을 읽는다. 그래서 "아직 저장소를 읽는 중"과 "확실히 로그아웃"을 구분하는 `ready` 상태를 둔다. `ready`가 되기 전에는 화면을 그리지 않고, `ready`이면서 토큰이 없을 때만 로그인으로 보낸다. 이렇게 하지 않으면 로그인된 사용자도 첫 렌더에서 로그인 화면으로 잘못 이동한다.

- [ ] **Step 4: 통과 확인**

Run: `npm test -- app/__tests__/page`
Expected: PASS 2개.

- [ ] **Step 5: 전체 테스트 + 빌드 확인**

Run:
```bash
cd /home/artyom9/project/Biblio-feat85-FE/frontend
npm test && npm run build
```
Expected: 모든 테스트 PASS, 빌드 성공.

- [ ] **Step 6: 수동 확인**

Run: `npm run dev` 후 브라우저에서:
- `/` 접속 → 로그인으로 이동되는지
- 가입 → 홈("내 프로젝트")으로 오는지
- 새로고침해도 로그인 유지되는지

- [ ] **Step 7: 커밋**

```bash
cd /home/artyom9/project/Biblio-feat85-FE
git add frontend
git commit -m "feat(fe): guard home route by auth"
```

---

## 1단계 완료 기준

- 가입·로그인이 Mock으로 끝까지 동작한다.
- 토큰이 없으면 보호된 홈에 접근할 수 없다.
- 모든 외부 호출이 `src/lib/api`를 지나고, 환경 변수로 Mock/실제를 전환할 수 있다.
- 전체 테스트가 통과하고 프로덕션 빌드가 성공한다.

이후 2단계(프로젝트 목록) 계획 문서로 이어 간다.
