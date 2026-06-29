# Frontend 구현 계획 — 4단계: 대화형 검색 + 플로팅 미니 플레이어

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 워크스페이스 오른쪽에서 대화형으로 검색하고, 답변의 출처를 누르면 떠 있는 미니 플레이어로 해당 영상 시점부터 재생한다.

**Architecture:** 검색은 매번 독립이며 질의·답변을 스레드로 쌓는다. 출처 선택은 상위 워크스페이스의 재생 상태를 바꾸고, 워크스페이스가 플로팅 플레이어를 띄운다. 플레이어는 배경을 가리지 않아 재생 중에도 검색·업로드가 가능하다. 검색과 재생 주소는 실제 백엔드를 호출하며, Mock은 프로젝트에 올린 영상으로 출처를 만든다.

**Tech Stack:** 이전 단계와 동일.

**작업 위치:** `/home/artyom9/project/Biblio-feat85-FE/frontend`.

**백엔드 계약(확인됨):** 검색 `POST /search`(게이트웨이 `/api/v1/search`), 본문 `{query, project_id}`, 응답 `{req_id, answer, chunks:[{ref, chunk_id, video_id, title, start_ms, end_ms, text, used}]}`. 재생 `POST /videos/{videoId}/playback-url` → `{signed_url}`.

---

## 파일 구조 (4단계)

| 경로 | 책임 |
|------|------|
| `src/lib/api/types.ts` (수정) | 검색 결과 타입·`Api` 검색/재생 메서드 |
| `src/lib/api/mock.ts` (수정) | 검색(프로젝트 영상 기반)·재생 URL Mock |
| `src/lib/api/http.ts` (수정) | 실제 검색·재생 호출과 매핑 |
| `src/components/SearchPanel.tsx` (생성) | 대화형 검색 스레드·출처 표시 |
| `src/components/FloatingPlayer.tsx` (생성) | 떠 있는 미니 플레이어 |
| `src/components/Workspace.tsx` (수정) | 검색 패널 연결 + 재생 상태 + 플레이어 |

---

## Task 1: 검색 타입 + Mock

**Files:**
- Modify: `src/lib/api/types.ts`
- Modify: `src/lib/api/mock.ts`
- Test: `src/lib/api/__tests__/mock-search.test.ts`

- [ ] **Step 1: 실패하는 테스트 작성**

Create `src/lib/api/__tests__/mock-search.test.ts`:
```ts
import { describe, it, expect, beforeEach } from "vitest";
import { createMockApi } from "@/lib/api/mock";

const PROJECT = "proj-1";

describe("mock search", () => {
  beforeEach(() => localStorage.clear());

  it("returns no results when the project has no videos", async () => {
    const res = await createMockApi().search(PROJECT, "임베딩");
    expect(res.chunks).toEqual([]);
    expect(res.answer).toContain("검색 결과가 없습니다");
  });

  it("returns chunks pointing at the project's videos", async () => {
    const api = createMockApi();
    const v = await api.uploadVideo(PROJECT, { kind: "url", sourceUrl: "https://x", title: "강의1" });
    const res = await api.search(PROJECT, "임베딩");
    expect(res.chunks.length).toBeGreaterThan(0);
    expect(res.chunks[0].videoId).toBe(v.id);
    expect(res.chunks[0].ref).toBe(1);
  });

  it("returns a playback url", async () => {
    const url = await createMockApi().getPlaybackUrl("v1");
    expect(typeof url).toBe("string");
    expect(url.length).toBeGreaterThan(0);
  });
});
```

- [ ] **Step 2: 실패 확인**

Run: `npm test -- mock-search`
Expected: FAIL — `search`/`getPlaybackUrl` 없음.

- [ ] **Step 3: 타입 추가**

`src/lib/api/types.ts`에서 `UploadVideoInput` 다음에 검색 타입을 추가하고, `Api`에 메서드 두 개를 더한다. 추가 타입:
```ts
export interface SearchChunk {
  ref: number;
  chunkId: string;
  videoId: string;
  title: string;
  startMs: number;
  endMs: number;
  text: string;
  used: boolean;
}

export interface SearchResult {
  reqId: string;
  answer: string;
  chunks: SearchChunk[];
}
```
`Api`에 추가:
```ts
  search(projectId: string, query: string): Promise<SearchResult>;
  getPlaybackUrl(videoId: string): Promise<string>;
```

- [ ] **Step 4: Mock 구현**

`src/lib/api/mock.ts`의 import에 `SearchResult`를 더하고, `createMockApi` return 객체의 `uploadVideo` 다음에 추가한다. import 수정:
```ts
import type {
  Api,
  AuthResponse,
  CreateProjectRequest,
  LoginRequest,
  Project,
  SearchResult,
  SignupRequest,
  UploadVideoInput,
  Video,
} from "./types";
```
메서드 추가:
```ts
    async search(projectId: string, query: string): Promise<SearchResult> {
      const videos = loadVideos()
        .filter((v) => v.projectId === projectId)
        .map(toVideo);
      if (videos.length === 0) {
        return { reqId: crypto.randomUUID(), answer: "검색 결과가 없습니다", chunks: [] };
      }
      const chunks = videos.slice(0, 3).map((v, i) => ({
        ref: i + 1,
        chunkId: crypto.randomUUID(),
        videoId: v.id,
        title: v.title,
        startMs: (i + 1) * 30000,
        endMs: (i + 1) * 30000 + 15000,
        text: `${v.title}에서 "${query}" 관련 구간`,
        used: true,
      }));
      return {
        reqId: crypto.randomUUID(),
        answer: `"${query}"에 대한 답변입니다. 관련 구간을 출처로 표시했습니다.`,
        chunks,
      };
    },

    async getPlaybackUrl(): Promise<string> {
      // 데모용 샘플 영상. 실제 백엔드 연결 시 서명 URL로 교체된다.
      return "https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/BigBuckBunny.mp4";
    },
```

- [ ] **Step 5: 통과 확인**

Run: `npm test -- mock-search` → PASS 3개. 회귀: `npm test -- mock`

- [ ] **Step 6: 커밋 (사용자가 직접)**

```bash
git add frontend && git commit -m "feat(fe): add search/playback to mock api"
```

---

## Task 2: 실제 HTTP 검색 + 재생

**Files:**
- Modify: `src/lib/api/http.ts`
- Test: `src/lib/api/__tests__/http-search.test.ts`

- [ ] **Step 1: 실패하는 테스트 작성**

Create `src/lib/api/__tests__/http-search.test.ts`:
```ts
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { createHttpApi } from "@/lib/api/http";
import { setToken } from "@/lib/auth/token";

afterEach(() => vi.restoreAllMocks());
beforeEach(() => localStorage.clear());

describe("http search", () => {
  it("search POSTs query+project_id and maps chunks", async () => {
    setToken("tok-1");
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          req_id: "r1",
          answer: "답변",
          chunks: [
            {
              ref: 1,
              chunk_id: "c1",
              video_id: "v1",
              title: "강의1",
              start_ms: 1000,
              end_ms: 2000,
              text: "조각",
              used: true,
            },
          ],
        }),
        { status: 200, headers: { "Content-Type": "application/json" } }
      )
    );
    vi.stubGlobal("fetch", fetchMock);

    const res = await createHttpApi("https://api.test").search("proj-1", "임베딩");

    expect(res.reqId).toBe("r1");
    expect(res.chunks[0]).toMatchObject({ chunkId: "c1", videoId: "v1", startMs: 1000 });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("https://api.test/api/v1/search");
    expect(JSON.parse(init.body)).toEqual({ query: "임베딩", project_id: "proj-1" });
  });

  it("getPlaybackUrl POSTs and returns the signed url", async () => {
    setToken("tok-1");
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ signed_url: "https://gcs/play", expires_at: "" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    );
    vi.stubGlobal("fetch", fetchMock);

    const url = await createHttpApi("https://api.test").getPlaybackUrl("v1");

    expect(url).toBe("https://gcs/play");
    const [reqUrl, init] = fetchMock.mock.calls[0];
    expect(reqUrl).toBe("https://api.test/videos/v1/playback-url");
    expect(init.method).toBe("POST");
  });
});
```

- [ ] **Step 2: 실패 확인**

Run: `npm test -- http-search`
Expected: FAIL — `search`/`getPlaybackUrl` 없음.

- [ ] **Step 3: HTTP 구현 확장**

`src/lib/api/http.ts`의 import에 `SearchResult`를 더하고, return 객체에 메서드를 추가한다. import 수정(타입 목록에 `SearchResult` 추가). `get` 함수 아래(영상 보조 함수들 근처)에 검색 응답 타입과 매핑을 추가한다:
```ts
  interface SearchChunkResponse {
    ref: number;
    chunk_id: string;
    video_id: string;
    title: string;
    start_ms: number;
    end_ms: number;
    text: string;
    used: boolean;
  }

  interface SearchResponse {
    req_id: string;
    answer: string;
    chunks: SearchChunkResponse[];
  }
```
return 객체에 추가:
```ts
    async search(projectId: string, query: string): Promise<SearchResult> {
      const res = await post<SearchResponse>("/api/v1/search", {
        query,
        project_id: projectId,
      });
      return {
        reqId: res.req_id,
        answer: res.answer,
        chunks: res.chunks.map((c) => ({
          ref: c.ref,
          chunkId: c.chunk_id,
          videoId: c.video_id,
          title: c.title,
          startMs: c.start_ms,
          endMs: c.end_ms,
          text: c.text,
          used: c.used,
        })),
      };
    },
    async getPlaybackUrl(videoId: string): Promise<string> {
      const res = await post<{ signed_url: string }>(`/videos/${videoId}/playback-url`, {});
      return res.signed_url;
    },
```

- [ ] **Step 4: 통과 확인**

Run: `npm test -- http-search` → PASS 2개. 회귀: `npm test -- http`

- [ ] **Step 5: 커밋 (사용자가 직접)**

```bash
git add frontend && git commit -m "feat(fe): add search/playback http client"
```

---

## Task 3: 대화형 검색 패널

**Files:**
- Create: `src/components/SearchPanel.tsx`
- Test: `src/components/__tests__/SearchPanel.test.tsx`

- [ ] **Step 1: 실패하는 테스트 작성**

Create `src/components/__tests__/SearchPanel.test.tsx`:
```tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const search = vi.fn();
vi.mock("@/lib/api", () => ({ api: { search: (p: string, q: string) => search(p, q) } }));

import { SearchPanel } from "@/components/SearchPanel";

describe("SearchPanel", () => {
  beforeEach(() => search.mockReset());

  it("runs a search and shows the answer and a citation", async () => {
    search.mockResolvedValue({
      reqId: "r1",
      answer: "이것이 답변입니다",
      chunks: [
        { ref: 1, chunkId: "c1", videoId: "v1", title: "강의1", startMs: 30000, endMs: 45000, text: "조각", used: true },
      ],
    });
    const onPlay = vi.fn();
    render(<SearchPanel projectId="p1" onPlay={onPlay} />);

    await userEvent.type(screen.getByLabelText("검색어"), "임베딩이 뭐야");
    await userEvent.click(screen.getByRole("button", { name: "검색" }));

    expect(search).toHaveBeenCalledWith("p1", "임베딩이 뭐야");
    expect(await screen.findByText("이것이 답변입니다")).toBeInTheDocument();

    const citation = await screen.findByRole("button", { name: /강의1/ });
    await userEvent.click(citation);
    expect(onPlay).toHaveBeenCalledWith(
      expect.objectContaining({ videoId: "v1", startMs: 30000 })
    );
  });
});
```

- [ ] **Step 2: 실패 확인**

Run: `npm test -- SearchPanel`
Expected: FAIL — 컴포넌트 없음.

- [ ] **Step 3: 구현**

Create `src/components/SearchPanel.tsx`:
```tsx
"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import type { SearchChunk, SearchResult } from "@/lib/api/types";

function formatMs(ms: number): string {
  const total = Math.floor(ms / 1000);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

interface Turn {
  query: string;
  result: SearchResult | null;
  error?: string;
}

export function SearchPanel({
  projectId,
  onPlay,
}: {
  projectId: string;
  onPlay: (chunk: SearchChunk) => void;
}) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    const q = query.trim();
    if (!q || loading) return;
    setQuery("");
    setLoading(true);
    const index = turns.length;
    setTurns((prev) => [...prev, { query: q, result: null }]);
    try {
      const result = await api.search(projectId, q);
      setTurns((prev) => prev.map((t, i) => (i === index ? { ...t, result } : t)));
    } catch {
      setTurns((prev) =>
        prev.map((t, i) => (i === index ? { ...t, error: "검색에 실패했습니다." } : t))
      );
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="flex h-screen flex-col p-4">
      <h2 className="mb-3 font-semibold">검색</h2>

      <div className="flex flex-1 flex-col gap-4 overflow-auto">
        {turns.map((turn, i) => (
          <div key={i} className="flex flex-col gap-2">
            <div className="self-end rounded bg-gray-100 px-3 py-2 text-sm">{turn.query}</div>
            {turn.error && <p className="text-sm text-red-600">{turn.error}</p>}
            {turn.result && (
              <div className="rounded border p-3 text-sm">
                <p>{turn.result.answer}</p>
                {turn.result.chunks.length > 0 && (
                  <ul className="mt-2 flex flex-col gap-1">
                    {turn.result.chunks.map((chunk) => (
                      <li key={chunk.chunkId}>
                        <button
                          type="button"
                          onClick={() => onPlay(chunk)}
                          className="text-left text-blue-600 underline"
                        >
                          [{chunk.ref}] {chunk.title} · {formatMs(chunk.startMs)}
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </div>
        ))}
      </div>

      <form onSubmit={onSubmit} className="mt-3 flex gap-2">
        <input
          aria-label="검색어"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="질문을 입력하세요"
          className="flex-1 rounded border p-2"
        />
        <button type="submit" disabled={loading} className="rounded bg-black px-4 text-sm text-white">
          검색
        </button>
      </form>
    </section>
  );
}
```

- [ ] **Step 4: 통과 확인**

Run: `npm test -- SearchPanel` → PASS 1개.

- [ ] **Step 5: 커밋 (사용자가 직접)**

```bash
git add frontend && git commit -m "feat(fe): add conversational search panel"
```

---

## Task 4: 플로팅 미니 플레이어

**Files:**
- Create: `src/components/FloatingPlayer.tsx`
- Test: `src/components/__tests__/FloatingPlayer.test.tsx`

- [ ] **Step 1: 실패하는 테스트 작성**

Create `src/components/__tests__/FloatingPlayer.test.tsx`:
```tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const getPlaybackUrl = vi.fn();
vi.mock("@/lib/api", () => ({ api: { getPlaybackUrl: (id: string) => getPlaybackUrl(id) } }));

import { FloatingPlayer } from "@/components/FloatingPlayer";

describe("FloatingPlayer", () => {
  beforeEach(() => getPlaybackUrl.mockReset());

  it("loads the playback url and shows the title", async () => {
    getPlaybackUrl.mockResolvedValue("https://play/url.mp4");
    render(
      <FloatingPlayer videoId="v1" startMs={30000} title="강의1" onClose={vi.fn()} />
    );
    expect(screen.getByText("강의1")).toBeInTheDocument();
    expect(getPlaybackUrl).toHaveBeenCalledWith("v1");
  });

  it("calls onClose when the close button is clicked", async () => {
    getPlaybackUrl.mockResolvedValue("https://play/url.mp4");
    const onClose = vi.fn();
    render(
      <FloatingPlayer videoId="v1" startMs={0} title="강의1" onClose={onClose} />
    );
    await userEvent.click(screen.getByRole("button", { name: "닫기" }));
    expect(onClose).toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: 실패 확인**

Run: `npm test -- FloatingPlayer`
Expected: FAIL — 컴포넌트 없음.

- [ ] **Step 3: 구현**

Create `src/components/FloatingPlayer.tsx`:
```tsx
"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";

export function FloatingPlayer({
  videoId,
  startMs,
  title,
  onClose,
}: {
  videoId: string;
  startMs: number;
  title: string;
  onClose: () => void;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [src, setSrc] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    api
      .getPlaybackUrl(videoId)
      .then((url) => {
        if (active) setSrc(url);
      })
      .catch(() => {
        if (active) setError("재생 주소를 가져오지 못했습니다.");
      });
    return () => {
      active = false;
    };
  }, [videoId]);

  function onLoadedMetadata() {
    const el = videoRef.current;
    if (!el) return;
    el.currentTime = startMs / 1000;
    void el.play().catch(() => {});
  }

  return (
    <div className="fixed bottom-4 right-4 z-50 w-80 overflow-hidden rounded-lg border bg-white shadow-lg">
      <div className="flex items-center justify-between border-b p-2 text-sm">
        <span className="truncate">{title}</span>
        <button type="button" onClick={onClose} aria-label="닫기" className="ml-2 shrink-0">
          ✕
        </button>
      </div>
      {error ? (
        <p className="p-3 text-sm text-red-600">{error}</p>
      ) : (
        <video
          ref={videoRef}
          src={src ?? undefined}
          onLoadedMetadata={onLoadedMetadata}
          controls
          className="w-full"
        />
      )}
    </div>
  );
}
```

- [ ] **Step 4: 통과 확인**

Run: `npm test -- FloatingPlayer` → PASS 2개.

- [ ] **Step 5: 커밋 (사용자가 직접)**

```bash
git add frontend && git commit -m "feat(fe): add floating mini player"
```

---

## Task 5: 워크스페이스에 연결

**Files:**
- Modify: `src/components/Workspace.tsx`

- [ ] **Step 1: 워크스페이스 갱신**

`src/components/Workspace.tsx`를 다음 내용으로 만든다(검색 자리표시를 `SearchPanel`로 바꾸고, 재생 상태와 플레이어를 추가):
```tsx
"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth/AuthContext";
import { VideoSourcePanel } from "@/components/VideoSourcePanel";
import { SearchPanel } from "@/components/SearchPanel";
import { FloatingPlayer } from "@/components/FloatingPlayer";

interface PlayRequest {
  videoId: string;
  startMs: number;
  title: string;
}

export function Workspace({ projectId }: { projectId: string }) {
  const router = useRouter();
  const { token, ready } = useAuth();
  const [playing, setPlaying] = useState<PlayRequest | null>(null);

  useEffect(() => {
    if (ready && token === null) {
      router.replace("/login");
    }
  }, [ready, token, router]);

  if (!ready || token === null) return null;

  return (
    <>
      <div className="grid h-screen grid-cols-[320px_1fr]">
        <VideoSourcePanel projectId={projectId} />
        <SearchPanel
          projectId={projectId}
          onPlay={(chunk) =>
            setPlaying({ videoId: chunk.videoId, startMs: chunk.startMs, title: chunk.title })
          }
        />
      </div>
      {playing && (
        <FloatingPlayer
          videoId={playing.videoId}
          startMs={playing.startMs}
          title={playing.title}
          onClose={() => setPlaying(null)}
        />
      )}
    </>
  );
}
```

- [ ] **Step 2: 기존 Workspace 테스트 확인**

Run: `npm test -- Workspace`
Expected: PASS 2개. (영상·검색 헤딩은 그대로, 플레이어는 초기엔 없음)

- [ ] **Step 3: 전체 테스트 + 빌드**

Run: `npm test && npm run build`
Expected: 모든 테스트 PASS, 빌드 성공.

- [ ] **Step 4: 수동 확인**

`npm run dev` 후:
- 영상 업로드 → 새로고침으로 완료
- 검색어 입력 → 답변 + 출처가 스레드로 쌓이는지
- 출처 클릭 → 오른쪽 아래 미니 플레이어가 뜨고, 그 상태로 검색·업로드가 계속 되는지
- 플레이어 닫기 동작

- [ ] **Step 5: 커밋 (사용자가 직접)**

```bash
git add frontend && git commit -m "feat(fe): wire search and player into workspace"
```

---

## 4단계 완료 기준

- 워크스페이스 오른쪽에서 검색하면 질의·답변이 스레드로 쌓인다(각 검색 독립).
- 답변의 출처를 누르면 떠 있는 미니 플레이어가 해당 시점부터 재생한다.
- 재생 중에도 검색·업로드가 막히지 않는다.
- 전체 테스트 통과, 빌드 성공.

남은 후속(별도): 멀티턴 대화, 플레이어 이동/크기조절, 자동 폴링, 그리고 백엔드 미구현분(가입·로그인 발급, 프로젝트 생성·목록, URL 업로드 외부 다운로드) 연결.
