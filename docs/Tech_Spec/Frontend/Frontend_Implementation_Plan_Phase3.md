# Frontend 구현 계획 — 3단계: 워크스페이스(영상 소스 · 업로드 · 처리 상태)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 프로젝트 워크스페이스에서 영상 목록과 처리 상태를 보고, 파일 또는 URL로 영상을 업로드할 수 있게 한다.

**Architecture:** 업로드는 `uploadVideo` 한 메서드로 추상화한다. 파일 업로드의 다단계 흐름(영상 생성 → 서명 URL 업로드 → 완료 통지)과 URL 업로드(생성)는 이 메서드 안에 숨긴다. Mock은 업로드된 영상을 처리중으로 두었다가 일정 시간이 지나면 완료로 바꾼다. 워크스페이스는 좌우 2분할이며, 왼쪽은 영상 소스 패널, 오른쪽은 검색 자리표시(4단계)다.

**Tech Stack:** 1·2단계와 동일.

**작업 위치:** `/home/artyom9/project/Biblio-feat85-FE/frontend`.

**백엔드 계약(확인됨):** 영상 생성 `POST /projects/{projectId}/videos`(본문은 `input_type`으로 분기: `LOCAL_FILE`은 `extension`, `EXTERNAL_URL`은 `source_url`), 파일은 응답의 서명 URL에 직접 업로드 후 `POST /videos/{videoId}/complete`. 목록 `GET /videos`. 상태값: `PENDING`·`UPLOADED`·`PROCESSING`·`READY`·`FAILED`·`DELETING`. 카테고리: `GENERAL`·`IT`·`MEDICAL`·`LEGAL`(이번 단계는 `GENERAL` 고정).

---

## 파일 구조 (3단계)

| 경로 | 책임 |
|------|------|
| `src/lib/api/types.ts` (수정) | 영상 타입·업로드 입력 타입·`Api` 영상 메서드 |
| `src/lib/api/mock.ts` (수정) | 영상 목록·업로드 Mock(localStorage, 시간 기반 처리→완료) |
| `src/lib/api/http.ts` (수정) | 실제 영상 목록·업로드(파일 다단계 / URL) |
| `src/components/VideoSourcePanel.tsx` (생성) | 영상 목록·상태·업로드(파일/URL 탭)·새로고침 |
| `src/components/Workspace.tsx` (생성) | 2분할 셸 + 인증 가드 |
| `src/app/projects/[id]/page.tsx` (수정) | 자리표시 대신 `Workspace` 렌더 |

---

## Task 1: 영상 타입 + Mock 업로드/목록

**Files:**
- Modify: `src/lib/api/types.ts`
- Modify: `src/lib/api/mock.ts`
- Test: `src/lib/api/__tests__/mock-videos.test.ts`

- [ ] **Step 1: 실패하는 테스트 작성**

Create `src/lib/api/__tests__/mock-videos.test.ts`:
```ts
import { describe, it, expect, beforeEach } from "vitest";
import { createMockApi } from "@/lib/api/mock";

const PROJECT = "proj-1";

describe("mock videos", () => {
  beforeEach(() => localStorage.clear());

  it("starts empty", async () => {
    expect(await createMockApi().listVideos(PROJECT)).toEqual([]);
  });

  it("uploads a url video as processing", async () => {
    const api = createMockApi();
    const v = await api.uploadVideo(PROJECT, {
      kind: "url",
      sourceUrl: "https://youtu.be/abc",
      title: "강의1",
    });
    expect(v.title).toBe("강의1");
    expect(v.inputType).toBe("EXTERNAL_URL");
    expect(v.status).toBe("PROCESSING");

    const list = await api.listVideos(PROJECT);
    expect(list).toHaveLength(1);
  });

  it("uploads a file video as processing", async () => {
    const api = createMockApi();
    const file = new File(["x"], "clip.mp4", { type: "video/mp4" });
    const v = await api.uploadVideo(PROJECT, { kind: "file", file, title: "강의2" });
    expect(v.inputType).toBe("LOCAL_FILE");
    expect(v.status).toBe("PROCESSING");
  });

  it("turns processing into ready after the processing window", async () => {
    const api = createMockApi();
    await api.uploadVideo(PROJECT, {
      kind: "url",
      sourceUrl: "https://youtu.be/abc",
      title: "강의1",
    });
    // 저장된 생성 시각을 과거로 돌려 처리 완료 조건을 만든다.
    const raw = JSON.parse(localStorage.getItem("biblio.mock.videos")!);
    raw[0].createdAt = new Date(Date.now() - 60_000).toISOString();
    localStorage.setItem("biblio.mock.videos", JSON.stringify(raw));

    const list = await api.listVideos(PROJECT);
    expect(list[0].status).toBe("READY");
  });

  it("scopes videos by project", async () => {
    const api = createMockApi();
    await api.uploadVideo(PROJECT, { kind: "url", sourceUrl: "https://x", title: "a" });
    expect(await api.listVideos("other")).toEqual([]);
  });
});
```

- [ ] **Step 2: 실패 확인**

Run: `npm test -- mock-videos`
Expected: FAIL — `listVideos`/`uploadVideo` 없음.

- [ ] **Step 3: 타입 추가**

`src/lib/api/types.ts`의 끝(`Api` 인터페이스 앞)에 영상 타입을 추가하고, `Api`에 영상 메서드 두 개를 더한다. 추가할 타입:
```ts
export type VideoStatus =
  | "PENDING"
  | "UPLOADED"
  | "PROCESSING"
  | "READY"
  | "FAILED"
  | "DELETING";

export type VideoInputType = "LOCAL_FILE" | "EXTERNAL_URL";

export interface Video {
  id: string;
  title: string;
  status: VideoStatus;
  inputType: VideoInputType;
  sourceUrl?: string;
  createdAt: string;
}

export type UploadVideoInput =
  | { kind: "file"; file: File; title: string }
  | { kind: "url"; sourceUrl: string; title: string };
```
`Api` 인터페이스에 추가:
```ts
  listVideos(projectId: string): Promise<Video[]>;
  uploadVideo(projectId: string, input: UploadVideoInput): Promise<Video>;
```

- [ ] **Step 4: Mock 구현**

`src/lib/api/mock.ts`에서 import에 새 타입을 더하고, 저장 헬퍼와 메서드를 추가한다. 파일 상단 import를 다음으로 바꾼다:
```ts
import type {
  Api,
  AuthResponse,
  CreateProjectRequest,
  LoginRequest,
  Project,
  SignupRequest,
  UploadVideoInput,
  Video,
} from "./types";
```
`PROJECTS_KEY` 아래에 영상 저장 헬퍼를 추가한다:
```ts
const VIDEOS_KEY = "biblio.mock.videos";
const PROCESSING_WINDOW_MS = 8000;

interface StoredVideo extends Video {
  projectId: string;
}

function loadVideos(): StoredVideo[] {
  if (typeof window === "undefined") return [];
  const raw = window.localStorage.getItem(VIDEOS_KEY);
  return raw ? (JSON.parse(raw) as StoredVideo[]) : [];
}

function saveVideos(videos: StoredVideo[]): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(VIDEOS_KEY, JSON.stringify(videos));
}

function toVideo(stored: StoredVideo): Video {
  const elapsed = Date.now() - new Date(stored.createdAt).getTime();
  const status =
    stored.status === "PROCESSING" && elapsed >= PROCESSING_WINDOW_MS
      ? "READY"
      : stored.status;
  return {
    id: stored.id,
    title: stored.title,
    status,
    inputType: stored.inputType,
    sourceUrl: stored.sourceUrl,
    createdAt: stored.createdAt,
  };
}
```
`createMockApi`의 return 객체 안, `createProject` 다음에 영상 메서드를 추가한다:
```ts
    async listVideos(projectId: string): Promise<Video[]> {
      const all = loadVideos();
      const mine = all.filter((v) => v.projectId === projectId);
      const upgraded = mine.map(toVideo);
      // 처리 완료로 바뀐 항목을 저장에 반영한다.
      let changed = false;
      const next = all.map((v) => {
        if (v.projectId !== projectId) return v;
        const fresh = upgraded.find((u) => u.id === v.id)!;
        if (fresh.status !== v.status) {
          changed = true;
          return { ...v, status: fresh.status };
        }
        return v;
      });
      if (changed) saveVideos(next);
      return upgraded;
    },

    async uploadVideo(projectId: string, input: UploadVideoInput): Promise<Video> {
      const stored: StoredVideo = {
        id: crypto.randomUUID(),
        projectId,
        title: input.title,
        status: "PROCESSING",
        inputType: input.kind === "file" ? "LOCAL_FILE" : "EXTERNAL_URL",
        sourceUrl: input.kind === "url" ? input.sourceUrl : undefined,
        createdAt: new Date().toISOString(),
      };
      const all = loadVideos();
      all.unshift(stored);
      saveVideos(all);
      return toVideo(stored);
    },
```

- [ ] **Step 5: 통과 확인**

Run: `npm test -- mock-videos` → PASS 5개. 기존 mock 회귀도 확인: `npm test -- mock`

- [ ] **Step 6: 커밋 (사용자가 직접)**

```bash
git add frontend && git commit -m "feat(fe): add video upload/list to mock api"
```

---

## Task 2: 실제 HTTP 영상 호출

**Files:**
- Modify: `src/lib/api/http.ts`
- Test: `src/lib/api/__tests__/http-videos.test.ts`

- [ ] **Step 1: 실패하는 테스트 작성**

Create `src/lib/api/__tests__/http-videos.test.ts`:
```ts
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { createHttpApi } from "@/lib/api/http";
import { setToken } from "@/lib/auth/token";

afterEach(() => vi.restoreAllMocks());
beforeEach(() => localStorage.clear());

describe("http videos", () => {
  it("listVideos GETs with the project filter and maps fields", async () => {
    setToken("tok-1");
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify([
          {
            video_id: "v1",
            title: "강의1",
            status: "READY",
            input_type: "EXTERNAL_URL",
            source_url: "https://x",
            created_at: "2026-01-01T00:00:00Z",
          },
        ]),
        { status: 200, headers: { "Content-Type": "application/json" } }
      )
    );
    vi.stubGlobal("fetch", fetchMock);

    const list = await createHttpApi("https://api.test").listVideos("proj-1");

    expect(list[0]).toMatchObject({ id: "v1", title: "강의1", status: "READY", inputType: "EXTERNAL_URL" });
    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe("https://api.test/videos?project_id=proj-1");
  });

  it("uploadVideo (url) POSTs an external-url create", async () => {
    setToken("tok-1");
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ video_id: "v2", status: "PENDING" }), {
        status: 202,
        headers: { "Content-Type": "application/json" },
      })
    );
    vi.stubGlobal("fetch", fetchMock);

    const v = await createHttpApi("https://api.test").uploadVideo("proj-1", {
      kind: "url",
      sourceUrl: "https://youtu.be/abc",
      title: "강의1",
    });

    expect(v.id).toBe("v2");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("https://api.test/projects/proj-1/videos");
    expect(JSON.parse(init.body)).toEqual({
      input_type: "EXTERNAL_URL",
      title: "강의1",
      category: "GENERAL",
      source_url: "https://youtu.be/abc",
    });
  });

  it("uploadVideo (file) creates, uploads to the signed url, then completes", async () => {
    setToken("tok-1");
    const calls: string[] = [];
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      calls.push(url);
      if (url.endsWith("/projects/proj-1/videos")) {
        return Promise.resolve(
          new Response(JSON.stringify({ video_id: "v3", status: "PENDING", signed_url: "https://gcs/upload" }), {
            status: 201,
            headers: { "Content-Type": "application/json" },
          })
        );
      }
      if (url === "https://gcs/upload") {
        return Promise.resolve(new Response(null, { status: 200 }));
      }
      // complete
      return Promise.resolve(
        new Response(JSON.stringify({ video_id: "v3", status: "UPLOADED" }), {
          status: 202,
          headers: { "Content-Type": "application/json" },
        })
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    const file = new File(["x"], "clip.mp4", { type: "video/mp4" });
    const v = await createHttpApi("https://api.test").uploadVideo("proj-1", {
      kind: "file",
      file,
      title: "강의2",
    });

    expect(v.id).toBe("v3");
    expect(calls[0]).toBe("https://api.test/projects/proj-1/videos");
    expect(calls[1]).toBe("https://gcs/upload");
    expect(calls[2]).toBe("https://api.test/videos/v3/complete");
  });
});
```

- [ ] **Step 2: 실패 확인**

Run: `npm test -- http-videos`
Expected: FAIL — `listVideos`/`uploadVideo` 없음.

- [ ] **Step 3: HTTP 구현 확장**

`src/lib/api/http.ts`에 import와 메서드를 추가한다. import 블록에 영상 타입을 더한다:
```ts
import type {
  Api,
  AuthResponse,
  CreateProjectRequest,
  LoginRequest,
  Project,
  SignupRequest,
  UploadVideoInput,
  Video,
  VideoInputType,
  VideoStatus,
} from "./types";
```
`createHttpApi` 안, `get` 함수 아래에 영상 보조 함수와 매핑을 추가한다:
```ts
  interface VideoResponse {
    video_id: string;
    title?: string;
    status: string;
    input_type?: string;
    source_url?: string | null;
    created_at?: string | null;
    signed_url?: string;
  }

  function mapVideo(res: VideoResponse, fallbackTitle: string): Video {
    return {
      id: res.video_id,
      title: res.title ?? fallbackTitle,
      status: (res.status as VideoStatus) ?? "PENDING",
      inputType: (res.input_type as VideoInputType) ?? "LOCAL_FILE",
      sourceUrl: res.source_url ?? undefined,
      createdAt: res.created_at ?? new Date().toISOString(),
    };
  }

  function fileExtension(name: string): string {
    const dot = name.lastIndexOf(".");
    return dot >= 0 ? name.slice(dot) : "";
  }

  async function uploadFile(projectId: string, file: File, title: string): Promise<Video> {
    const created = await post<VideoResponse>(`/projects/${projectId}/videos`, {
      input_type: "LOCAL_FILE",
      title,
      category: "GENERAL",
      extension: fileExtension(file.name),
    });
    if (created.signed_url) {
      const put = await fetch(created.signed_url, { method: "PUT", body: file });
      if (!put.ok) throw new Error(`업로드 실패 (${put.status})`);
    }
    const completed = await post<VideoResponse>(`/videos/${created.video_id}/complete`, {});
    return mapVideo({ ...created, ...completed }, title);
  }

  async function uploadUrl(projectId: string, sourceUrl: string, title: string): Promise<Video> {
    const created = await post<VideoResponse>(`/projects/${projectId}/videos`, {
      input_type: "EXTERNAL_URL",
      title,
      category: "GENERAL",
      source_url: sourceUrl,
    });
    return mapVideo(created, title);
  }
```
return 객체에 메서드를 추가한다:
```ts
    async listVideos(projectId: string): Promise<Video[]> {
      const items = await get<VideoResponse[]>(`/videos?project_id=${projectId}`);
      return items.map((item) => mapVideo(item, item.title ?? ""));
    },
    uploadVideo(projectId: string, input: UploadVideoInput): Promise<Video> {
      return input.kind === "file"
        ? uploadFile(projectId, input.file, input.title)
        : uploadUrl(projectId, input.sourceUrl, input.title);
    },
```

- [ ] **Step 4: 통과 확인**

Run: `npm test -- http-videos` → PASS 3개. 회귀: `npm test -- http`

- [ ] **Step 5: 커밋 (사용자가 직접)**

```bash
git add frontend && git commit -m "feat(fe): add video http client"
```

---

## Task 3: 영상 소스 패널

**Files:**
- Create: `src/components/VideoSourcePanel.tsx`
- Test: `src/components/__tests__/VideoSourcePanel.test.tsx`

- [ ] **Step 1: 실패하는 테스트 작성**

Create `src/components/__tests__/VideoSourcePanel.test.tsx`:
```tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const listVideos = vi.fn();
const uploadVideo = vi.fn();
vi.mock("@/lib/api", () => ({
  api: {
    listVideos: (p: string) => listVideos(p),
    uploadVideo: (p: string, i: unknown) => uploadVideo(p, i),
  },
}));

import { VideoSourcePanel } from "@/components/VideoSourcePanel";

describe("VideoSourcePanel", () => {
  beforeEach(() => {
    listVideos.mockReset();
    uploadVideo.mockReset();
  });

  it("lists videos with a status label", async () => {
    listVideos.mockResolvedValue([
      { id: "v1", title: "강의1", status: "PROCESSING", inputType: "EXTERNAL_URL", createdAt: "" },
      { id: "v2", title: "강의2", status: "READY", inputType: "LOCAL_FILE", createdAt: "" },
    ]);
    render(<VideoSourcePanel projectId="p1" />);
    expect(await screen.findByText("강의1")).toBeInTheDocument();
    expect(screen.getByText("처리중")).toBeInTheDocument();
    expect(screen.getByText("완료")).toBeInTheDocument();
  });

  it("uploads a URL video and shows it", async () => {
    listVideos.mockResolvedValue([]);
    uploadVideo.mockResolvedValue({
      id: "v9", title: "새영상", status: "PROCESSING", inputType: "EXTERNAL_URL", createdAt: "",
    });
    render(<VideoSourcePanel projectId="p1" />);

    await userEvent.click(await screen.findByRole("button", { name: "URL" }));
    await userEvent.type(screen.getByLabelText("영상 제목"), "새영상");
    await userEvent.type(screen.getByLabelText("영상 URL"), "https://youtu.be/abc");
    await userEvent.click(screen.getByRole("button", { name: "업로드" }));

    expect(uploadVideo).toHaveBeenCalledWith("p1", {
      kind: "url",
      sourceUrl: "https://youtu.be/abc",
      title: "새영상",
    });
    expect(await screen.findByText("새영상")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: 실패 확인**

Run: `npm test -- VideoSourcePanel`
Expected: FAIL — 컴포넌트 없음.

- [ ] **Step 3: 구현**

Create `src/components/VideoSourcePanel.tsx`:
```tsx
"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { UploadVideoInput, Video, VideoStatus } from "@/lib/api/types";

const STATUS_LABEL: Record<VideoStatus, string> = {
  PENDING: "대기",
  UPLOADED: "업로드됨",
  PROCESSING: "처리중",
  READY: "완료",
  FAILED: "실패",
  DELETING: "삭제 중",
};

export function VideoSourcePanel({ projectId }: { projectId: string }) {
  const [videos, setVideos] = useState<Video[]>([]);
  const [tab, setTab] = useState<"file" | "url">("file");
  const [title, setTitle] = useState("");
  const [sourceUrl, setSourceUrl] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    api
      .listVideos(projectId)
      .then(setVideos)
      .catch(() => setError("영상을 불러오지 못했습니다."));
  }, [projectId]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function onUpload(e: React.FormEvent) {
    e.preventDefault();
    const name = title.trim();
    if (!name) return;
    let input: UploadVideoInput;
    if (tab === "url") {
      const url = sourceUrl.trim();
      if (!url) return;
      input = { kind: "url", sourceUrl: url, title: name };
    } else {
      if (!file) return;
      input = { kind: "file", file, title: name };
    }
    try {
      const video = await api.uploadVideo(projectId, input);
      setVideos((prev) => [video, ...prev]);
      setTitle("");
      setSourceUrl("");
      setFile(null);
    } catch {
      setError("업로드에 실패했습니다.");
    }
  }

  return (
    <section className="flex h-full flex-col gap-4 border-r p-4">
      <div className="flex items-center justify-between">
        <h2 className="font-semibold">영상</h2>
        <button type="button" onClick={refresh} className="text-xs text-gray-500 underline">
          새로고침
        </button>
      </div>

      <form onSubmit={onUpload} className="flex flex-col gap-2 rounded border p-3">
        <div className="flex gap-2 text-sm">
          <button
            type="button"
            onClick={() => setTab("file")}
            className={tab === "file" ? "font-semibold underline" : "text-gray-500"}
          >
            파일
          </button>
          <button
            type="button"
            onClick={() => setTab("url")}
            className={tab === "url" ? "font-semibold underline" : "text-gray-500"}
          >
            URL
          </button>
        </div>

        <label className="flex flex-col gap-1 text-sm">
          영상 제목
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            className="rounded border p-2"
          />
        </label>

        {tab === "url" ? (
          <label key="url-field" className="flex flex-col gap-1 text-sm">
            영상 URL
            <input
              value={sourceUrl}
              onChange={(e) => setSourceUrl(e.target.value)}
              placeholder="https://..."
              className="rounded border p-2"
            />
          </label>
        ) : (
          <label key="file-field" className="flex flex-col gap-1 text-sm">
            영상 파일
            <input
              type="file"
              accept="video/*"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              className="text-sm"
            />
          </label>
        )}

        <button type="submit" className="rounded bg-black px-3 py-1 text-sm text-white">
          업로드
        </button>
      </form>

      {error && <p className="text-sm text-red-600">{error}</p>}

      <ul className="flex flex-col gap-2 overflow-auto">
        {videos.map((video) => (
          <li key={video.id} className="flex items-center justify-between rounded border p-2 text-sm">
            <span className="truncate">{video.title}</span>
            <span className="ml-2 shrink-0 text-xs text-gray-500">
              {STATUS_LABEL[video.status]}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}
```

- [ ] **Step 4: 통과 확인**

Run: `npm test -- VideoSourcePanel` → PASS 2개.

- [ ] **Step 5: 커밋 (사용자가 직접)**

```bash
git add frontend && git commit -m "feat(fe): add video source panel"
```

---

## Task 4: 워크스페이스 셸 + 라우트 연결

**Files:**
- Create: `src/components/Workspace.tsx`
- Modify: `src/app/projects/[id]/page.tsx`
- Test: `src/components/__tests__/Workspace.test.tsx`

- [ ] **Step 1: 실패하는 테스트 작성**

Create `src/components/__tests__/Workspace.test.tsx`:
```tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";

const replace = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace }) }));
vi.mock("@/lib/api", () => ({ api: { listVideos: () => Promise.resolve([]) } }));

import { Workspace } from "@/components/Workspace";
import { AuthProvider } from "@/lib/auth/AuthContext";
import { setToken } from "@/lib/auth/token";

function renderWorkspace() {
  return render(
    <AuthProvider>
      <Workspace projectId="p1" />
    </AuthProvider>
  );
}

describe("Workspace", () => {
  beforeEach(() => {
    replace.mockReset();
    localStorage.clear();
  });

  it("redirects to /login when signed out", () => {
    renderWorkspace();
    expect(replace).toHaveBeenCalledWith("/login");
  });

  it("shows the video panel and search placeholder when signed in", () => {
    setToken("t1");
    renderWorkspace();
    expect(replace).not.toHaveBeenCalled();
    expect(screen.getByRole("heading", { name: "영상" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "검색" })).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: 실패 확인**

Run: `npm test -- Workspace`
Expected: FAIL — 컴포넌트 없음.

- [ ] **Step 3: 구현**

Create `src/components/Workspace.tsx`:
```tsx
"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth/AuthContext";
import { VideoSourcePanel } from "@/components/VideoSourcePanel";

export function Workspace({ projectId }: { projectId: string }) {
  const router = useRouter();
  const { token, ready } = useAuth();

  useEffect(() => {
    if (ready && token === null) {
      router.replace("/login");
    }
  }, [ready, token, router]);

  if (!ready || token === null) return null;

  return (
    <div className="grid h-screen grid-cols-[320px_1fr]">
      <VideoSourcePanel projectId={projectId} />
      <section className="p-6">
        <h2 className="font-semibold">검색</h2>
        <p className="mt-2 text-sm text-gray-500">
          대화형 검색은 다음 단계에서 구현합니다.
        </p>
      </section>
    </div>
  );
}
```

- [ ] **Step 4: 라우트 연결**

`src/app/projects/[id]/page.tsx`를 다음 내용으로 만든다:
```tsx
import { Workspace } from "@/components/Workspace";

export default async function ProjectWorkspacePage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <Workspace projectId={id} />;
}
```

- [ ] **Step 5: 통과 확인**

Run: `npm test -- Workspace` → PASS 2개.

- [ ] **Step 6: 전체 테스트 + 빌드**

Run: `npm test && npm run build`
Expected: 모든 테스트 PASS, 빌드 성공.

- [ ] **Step 7: 수동 확인**

`npm run dev` 후:
- 프로젝트 카드 클릭 → 워크스페이스(왼쪽 영상 패널, 오른쪽 검색 자리표시)
- URL 탭으로 영상 업로드 → "처리중"으로 목록에 추가
- 잠시 뒤 "새로고침" 클릭 → "완료"로 바뀌는지
- 파일 탭으로도 업로드되는지

- [ ] **Step 8: 커밋 (사용자가 직접)**

```bash
git add frontend && git commit -m "feat(fe): add project workspace"
```

---

## 3단계 완료 기준

- 워크스페이스가 2분할로 뜨고, 왼쪽에 영상 목록·상태·업로드가 있다.
- 파일/URL 두 방식으로 영상을 업로드하면 목록에 처리중으로 추가된다.
- 새로고침하면 처리중이 완료로 바뀐다(Mock).
- 로그아웃 상태로 워크스페이스에 직접 들어가면 로그인으로 이동한다.
- 전체 테스트 통과, 빌드 성공.

이후 4단계(대화형 검색 + 플로팅 미니 플레이어)로 이어 간다.
