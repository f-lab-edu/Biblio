# Frontend 구현 계획 — 2단계: 프로젝트 목록 + 생성

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 로그인 후 홈에서 내 프로젝트를 카드 그리드로 보고, 새 프로젝트를 만들 수 있게 한다(Mock).

**Architecture:** 1단계의 API 경계 계층(`src/lib/api`)을 확장한다. 프로젝트 조회·생성 메서드를 `Api`에 추가하고, Mock 구현은 localStorage에 저장해 새로고침해도 유지되게 한다. 홈 화면은 프로젝트 목록 컴포넌트를 그리고, 프로젝트 카드를 누르면 워크스페이스 자리표시 화면으로 이동한다.

**Tech Stack:** 1단계와 동일 (Next.js 16 App Router · TypeScript · Tailwind · Vitest + RTL).

**작업 위치:** `/home/artyom9/project/Biblio-feat85-FE/frontend` (모든 경로는 이 디렉토리 기준).

**선행 조건:** 1단계 완료 (`Frontend_Implementation_Plan.md`).

---

## 파일 구조 (2단계에서 생성·수정)

| 경로 | 책임 |
|------|------|
| `src/lib/api/types.ts` (수정) | `Project`·`CreateProjectRequest` 타입과 `Api`에 프로젝트 메서드 추가 |
| `src/lib/api/mock.ts` (수정) | 프로젝트 조회·생성 Mock, localStorage 저장 |
| `src/lib/api/http.ts` (수정) | 프로젝트 조회·생성의 실제 HTTP 호출(인증 헤더 포함) |
| `src/components/ProjectList.tsx` (생성) | 카드 그리드 + 새 프로젝트 생성 폼 |
| `src/app/page.tsx` (수정) | 자리표시 대신 `ProjectList` 렌더 |
| `src/app/projects/[id]/page.tsx` (생성) | 워크스페이스 자리표시(3단계에서 구현) |

---

## Task 1: API 타입 + Mock 프로젝트 (localStorage)

**Files:**
- Modify: `src/lib/api/types.ts`
- Modify: `src/lib/api/mock.ts`
- Test: `src/lib/api/__tests__/mock-projects.test.ts`

- [ ] **Step 1: 실패하는 테스트 작성**

Create `src/lib/api/__tests__/mock-projects.test.ts`:
```ts
import { describe, it, expect, beforeEach } from "vitest";
import { createMockApi } from "@/lib/api/mock";

describe("mock projects", () => {
  beforeEach(() => localStorage.clear());

  it("starts with no projects", async () => {
    const api = createMockApi();
    expect(await api.listProjects()).toEqual([]);
  });

  it("creates a project and lists it", async () => {
    const api = createMockApi();
    const created = await api.createProject({ title: "강의영상" });
    expect(created.id).toBeTruthy();
    expect(created.title).toBe("강의영상");
    expect(created.videoCount).toBe(0);

    const list = await api.listProjects();
    expect(list).toHaveLength(1);
    expect(list[0].title).toBe("강의영상");
  });

  it("persists projects across api instances (localStorage)", async () => {
    await createMockApi().createProject({ title: "회의록" });
    const list = await createMockApi().listProjects();
    expect(list.map((p) => p.title)).toContain("회의록");
  });
});
```

- [ ] **Step 2: 실패 확인**

Run: `npm test -- mock-projects`
Expected: FAIL — `listProjects`/`createProject` 없음.

- [ ] **Step 3: 타입 추가**

`src/lib/api/types.ts`에 추가하고 `Api`를 확장한다. 파일을 다음 내용으로 만든다:
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

export interface Project {
  id: string;
  title: string;
  videoCount: number;
  createdAt: string;
  updatedAt: string;
}

export interface CreateProjectRequest {
  title: string;
}

export interface Api {
  signup(req: SignupRequest): Promise<AuthResponse>;
  login(req: LoginRequest): Promise<AuthResponse>;
  listProjects(): Promise<Project[]>;
  createProject(req: CreateProjectRequest): Promise<Project>;
}
```

- [ ] **Step 4: Mock 구현**

`src/lib/api/mock.ts`를 다음 내용으로 만든다:
```ts
import type {
  Api,
  AuthResponse,
  CreateProjectRequest,
  LoginRequest,
  Project,
  SignupRequest,
} from "./types";

interface MockUser {
  userId: string;
  email: string;
  password: string;
}

const PROJECTS_KEY = "biblio.mock.projects";

function loadProjects(): Project[] {
  if (typeof window === "undefined") return [];
  const raw = window.localStorage.getItem(PROJECTS_KEY);
  return raw ? (JSON.parse(raw) as Project[]) : [];
}

function saveProjects(projects: Project[]): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(PROJECTS_KEY, JSON.stringify(projects));
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

    async listProjects(): Promise<Project[]> {
      return loadProjects();
    },

    async createProject({ title }: CreateProjectRequest): Promise<Project> {
      const now = new Date().toISOString();
      const project: Project = {
        id: crypto.randomUUID(),
        title,
        videoCount: 0,
        createdAt: now,
        updatedAt: now,
      };
      const projects = loadProjects();
      projects.unshift(project);
      saveProjects(projects);
      return project;
    },
  };
}
```

- [ ] **Step 5: 통과 확인**

Run: `npm test -- mock-projects`
Expected: PASS 3개. 기존 `mock` 테스트도 깨지지 않았는지 함께 확인: `npm test -- mock`

- [ ] **Step 6: 커밋 (사용자가 직접)**

```bash
git add frontend
git commit -m "feat(fe): add project list/create to mock api"
```

---

## Task 2: 실제 HTTP 프로젝트 호출

백엔드 프로젝트 API는 아직 없으므로 호출 형태만 맞춰 둔다. 인증 토큰을 헤더에 싣는다.

**Files:**
- Modify: `src/lib/api/http.ts`
- Test: `src/lib/api/__tests__/http-projects.test.ts`

- [ ] **Step 1: 실패하는 테스트 작성**

Create `src/lib/api/__tests__/http-projects.test.ts`:
```ts
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { createHttpApi } from "@/lib/api/http";
import { setToken } from "@/lib/auth/token";

afterEach(() => vi.restoreAllMocks());
beforeEach(() => localStorage.clear());

describe("http projects", () => {
  it("listProjects GETs with the bearer token", async () => {
    setToken("tok-1");
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify([{ id: "p1", title: "x", videoCount: 0, createdAt: "", updatedAt: "" }]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    );
    vi.stubGlobal("fetch", fetchMock);

    const api = createHttpApi("https://api.test");
    const list = await api.listProjects();

    expect(list).toHaveLength(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("https://api.test/api/v1/projects");
    expect(init.method).toBe("GET");
    expect(init.headers.Authorization).toBe("Bearer tok-1");
  });

  it("createProject POSTs the title", async () => {
    setToken("tok-1");
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ id: "p2", title: "회의록", videoCount: 0, createdAt: "", updatedAt: "" }), {
        status: 201,
        headers: { "Content-Type": "application/json" },
      })
    );
    vi.stubGlobal("fetch", fetchMock);

    const api = createHttpApi("https://api.test");
    const created = await api.createProject({ title: "회의록" });

    expect(created.id).toBe("p2");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("https://api.test/api/v1/projects");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ title: "회의록" });
  });
});
```

- [ ] **Step 2: 실패 확인**

Run: `npm test -- http-projects`
Expected: FAIL — `listProjects`/`createProject` 없음.

- [ ] **Step 3: HTTP 구현 확장**

`src/lib/api/http.ts`를 다음 내용으로 만든다:
```ts
import type {
  Api,
  AuthResponse,
  CreateProjectRequest,
  LoginRequest,
  Project,
  SignupRequest,
} from "./types";
import { getToken } from "@/lib/auth/token";

export function createHttpApi(baseUrl: string): Api {
  function authHeaders(): Record<string, string> {
    const token = getToken();
    return token ? { Authorization: `Bearer ${token}` } : {};
  }

  async function request<T>(path: string, init: RequestInit): Promise<T> {
    const res = await fetch(`${baseUrl}${path}`, init);
    if (!res.ok) {
      throw new Error(`요청 실패 (${res.status})`);
    }
    return (await res.json()) as T;
  }

  function post<T>(path: string, body: unknown): Promise<T> {
    return request<T>(path, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify(body),
    });
  }

  function get<T>(path: string): Promise<T> {
    return request<T>(path, { method: "GET", headers: { ...authHeaders() } });
  }

  return {
    signup(req: SignupRequest): Promise<AuthResponse> {
      return post<AuthResponse>("/api/v1/auth/signup", req);
    },
    login(req: LoginRequest): Promise<AuthResponse> {
      return post<AuthResponse>("/api/v1/auth/login", req);
    },
    listProjects(): Promise<Project[]> {
      return get<Project[]>("/api/v1/projects");
    },
    createProject(req: CreateProjectRequest): Promise<Project> {
      return post<Project>("/api/v1/projects", req);
    },
  };
}
```

- [ ] **Step 4: 통과 확인**

Run: `npm test -- http-projects`
Expected: PASS 2개. 기존 `http` 테스트도 확인: `npm test -- http`

- [ ] **Step 5: 커밋 (사용자가 직접)**

```bash
git add frontend
git commit -m "feat(fe): add project http client"
```

---

## Task 3: 프로젝트 목록 컴포넌트

**Files:**
- Create: `src/components/ProjectList.tsx`
- Test: `src/components/__tests__/ProjectList.test.tsx`

- [ ] **Step 1: 실패하는 테스트 작성**

Create `src/components/__tests__/ProjectList.test.tsx`:
```tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const listProjects = vi.fn();
const createProject = vi.fn();
vi.mock("@/lib/api", () => ({
  api: {
    listProjects: () => listProjects(),
    createProject: (req: unknown) => createProject(req),
  },
}));

import { ProjectList } from "@/components/ProjectList";

describe("ProjectList", () => {
  beforeEach(() => {
    listProjects.mockReset();
    createProject.mockReset();
  });

  it("renders existing projects", async () => {
    listProjects.mockResolvedValue([
      { id: "p1", title: "강의영상", videoCount: 3, createdAt: "", updatedAt: "" },
    ]);
    render(<ProjectList />);
    expect(await screen.findByText("강의영상")).toBeInTheDocument();
    expect(screen.getByText("영상 3개")).toBeInTheDocument();
  });

  it("creates a new project and shows it", async () => {
    listProjects.mockResolvedValue([]);
    createProject.mockResolvedValue({
      id: "p2",
      title: "회의록",
      videoCount: 0,
      createdAt: "",
      updatedAt: "",
    });
    render(<ProjectList />);

    await userEvent.click(await screen.findByRole("button", { name: "＋ 새 프로젝트" }));
    await userEvent.type(screen.getByLabelText("프로젝트 제목"), "회의록");
    await userEvent.click(screen.getByRole("button", { name: "만들기" }));

    expect(createProject).toHaveBeenCalledWith({ title: "회의록" });
    expect(await screen.findByText("회의록")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: 실패 확인**

Run: `npm test -- ProjectList`
Expected: FAIL — 컴포넌트 없음.

- [ ] **Step 3: 구현**

Create `src/components/ProjectList.tsx`:
```tsx
"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import type { Project } from "@/lib/api/types";

export function ProjectList() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [creating, setCreating] = useState(false);
  const [title, setTitle] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .listProjects()
      .then(setProjects)
      .catch(() => setError("프로젝트를 불러오지 못했습니다."));
  }, []);

  async function onCreate(e: React.FormEvent) {
    e.preventDefault();
    const name = title.trim();
    if (!name) return;
    try {
      const project = await api.createProject({ title: name });
      setProjects((prev) => [project, ...prev]);
      setTitle("");
      setCreating(false);
    } catch {
      setError("프로젝트 생성에 실패했습니다.");
    }
  }

  return (
    <main className="p-6">
      <h1 className="mb-4 text-2xl font-bold">내 프로젝트</h1>
      {error && <p className="mb-3 text-sm text-red-600">{error}</p>}
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 md:grid-cols-4">
        <div className="flex min-h-28 items-center justify-center rounded border border-dashed p-4">
          {creating ? (
            <form onSubmit={onCreate} className="flex w-full flex-col gap-2">
              <input
                aria-label="프로젝트 제목"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="제목"
                autoFocus
                className="rounded border p-2"
              />
              <div className="flex gap-2">
                <button type="submit" className="rounded bg-black px-3 py-1 text-sm text-white">
                  만들기
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setCreating(false);
                    setTitle("");
                  }}
                  className="rounded border px-3 py-1 text-sm"
                >
                  취소
                </button>
              </div>
            </form>
          ) : (
            <button
              type="button"
              onClick={() => setCreating(true)}
              className="text-sm font-medium text-gray-600"
            >
              ＋ 새 프로젝트
            </button>
          )}
        </div>

        {projects.map((project) => (
          <Link
            key={project.id}
            href={`/projects/${project.id}`}
            className="flex min-h-28 flex-col justify-between rounded border p-4 hover:bg-gray-50"
          >
            <span className="font-medium">{project.title}</span>
            <span className="mt-2 text-xs text-gray-500">영상 {project.videoCount}개</span>
          </Link>
        ))}
      </div>
    </main>
  );
}
```

- [ ] **Step 4: 통과 확인**

Run: `npm test -- ProjectList`
Expected: PASS 2개.

- [ ] **Step 5: 커밋 (사용자가 직접)**

```bash
git add frontend
git commit -m "feat(fe): add project list component"
```

---

## Task 4: 홈에 연결 + 워크스페이스 자리표시

**Files:**
- Modify: `src/app/page.tsx`
- Create: `src/app/projects/[id]/page.tsx`
- Test: `src/app/__tests__/page.test.tsx` (수정)

- [ ] **Step 1: 홈 테스트 갱신**

`src/app/__tests__/page.test.tsx`에서 "signed in" 케이스가 `ProjectList`를 거치므로 api를 Mock 처리한다. 파일을 다음 내용으로 만든다:
```tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";

const replace = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace }) }));
vi.mock("@/lib/api", () => ({
  api: { listProjects: () => Promise.resolve([]), createProject: vi.fn() },
}));

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

  it("shows the project list when signed in", () => {
    setToken("t1");
    renderHome();
    expect(replace).not.toHaveBeenCalled();
    expect(screen.getByText("내 프로젝트")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: 실패 확인**

Run: `npm test -- app/__tests__/page`
Expected: 현재 홈은 자리표시 문구를 직접 들고 있어 통과할 수도 있다. 다음 단계에서 `ProjectList`로 바꾼 뒤 다시 확인한다.

- [ ] **Step 3: 홈을 ProjectList로 교체**

`src/app/page.tsx`를 다음 내용으로 만든다:
```tsx
"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth/AuthContext";
import { ProjectList } from "@/components/ProjectList";

export default function Home() {
  const router = useRouter();
  const { token, ready } = useAuth();

  useEffect(() => {
    if (ready && token === null) {
      router.replace("/login");
    }
  }, [ready, token, router]);

  if (!ready || token === null) return null;

  return <ProjectList />;
}
```

- [ ] **Step 4: 워크스페이스 자리표시 생성**

Create `src/app/projects/[id]/page.tsx`:
```tsx
export default async function ProjectWorkspace({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return (
    <main className="p-6">
      <h1 className="text-2xl font-bold">워크스페이스</h1>
      <p className="mt-2 text-sm text-gray-500">
        프로젝트 {id} — 영상 업로드와 검색은 다음 단계에서 구현합니다.
      </p>
    </main>
  );
}
```

- [ ] **Step 5: 통과 확인**

Run: `npm test -- app/__tests__/page`
Expected: PASS 2개.

- [ ] **Step 6: 전체 테스트 + 빌드**

Run:
```bash
npm test && npm run build
```
Expected: 모든 테스트 PASS, 빌드 성공(라우트에 `/projects/[id]` 추가).

- [ ] **Step 7: 수동 확인**

`npm run dev` 후:
- 로그인 → "내 프로젝트"에 "＋ 새 프로젝트" 카드
- 새 프로젝트 만들면 카드가 추가되는지
- 새로고침해도 프로젝트가 남는지(localStorage)
- 카드 클릭 → 워크스페이스 자리표시로 이동하는지

- [ ] **Step 8: 커밋 (사용자가 직접)**

```bash
git add frontend
git commit -m "feat(fe): wire project list into home"
```

---

## 2단계 완료 기준

- 로그인 후 프로젝트를 카드 그리드로 본다.
- 새 프로젝트를 만들면 목록에 즉시 추가되고, 새로고침해도 유지된다(Mock).
- 프로젝트 카드를 누르면 워크스페이스 자리표시로 이동한다.
- 전체 테스트 통과, 프로덕션 빌드 성공.

이후 3단계(워크스페이스 — 영상 소스·업로드·처리 상태) 계획 문서로 이어 간다.
