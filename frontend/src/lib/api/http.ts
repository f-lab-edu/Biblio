import type {
  Api,
  AuthResponse,
  CreateProjectRequest,
  CurrentUserResponse,
  FeedbackRating,
  LoginRequest,
  Project,
  SearchHistoryTurn,
  SearchResult,
  SignupRequest,
  UploadHeaders,
  UploadVideoInput,
  UploadVideoOptions,
  Video,
  VideoInputType,
  VideoStatus,
} from "./types";
import { getCsrfToken } from "@/lib/auth/token";
import { normalizedFileExtension } from "./upload-constraints";

export class HttpError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly code?: string,
    public readonly traceId?: string
  ) {
    super(message);
    this.name = "HttpError";
  }
}

interface ApiErrorPayload {
  code: string;
  message: string;
  trace_id: string;
}

function isApiErrorPayload(value: unknown): value is ApiErrorPayload {
  if (typeof value !== "object" || value === null) return false;
  const payload = value as Record<string, unknown>;
  return (
    typeof payload.code === "string" &&
    typeof payload.message === "string" &&
    typeof payload.trace_id === "string"
  );
}

async function readApiErrorPayload(response: Response): Promise<ApiErrorPayload | undefined> {
  try {
    const payload: unknown = await response.json();
    return isApiErrorPayload(payload) ? payload : undefined;
  } catch {
    return undefined;
  }
}

async function toHttpError(response: Response): Promise<HttpError> {
  const payload = await readApiErrorPayload(response);
  if (payload) {
    return new HttpError(payload.message, response.status, payload.code, payload.trace_id);
  }
  const traceId = response.headers.get("X-Trace-Id") ?? undefined;
  return new HttpError(`요청 실패 (${response.status})`, response.status, undefined, traceId);
}

let activeSignedUrlUploads = 0;
let beforeUnloadHandler: ((event: BeforeUnloadEvent) => void) | null = null;

function registerUploadWarning() {
  if (typeof window === "undefined") return;
  activeSignedUrlUploads += 1;
  if (beforeUnloadHandler) return;
  beforeUnloadHandler = (event: BeforeUnloadEvent) => {
    event.preventDefault();
    event.returnValue = "";
  };
  window.addEventListener("beforeunload", beforeUnloadHandler);
}

function unregisterUploadWarning() {
  if (typeof window === "undefined") return;
  activeSignedUrlUploads = Math.max(0, activeSignedUrlUploads - 1);
  if (activeSignedUrlUploads > 0 || !beforeUnloadHandler) return;
  window.removeEventListener("beforeunload", beforeUnloadHandler);
  beforeUnloadHandler = null;
}

function clampPercent(value: number) {
  return Math.max(0, Math.min(100, value));
}

export function createHttpApi(baseUrl: string): Api {
  function csrfHeaders(): Record<string, string> {
    const token = getCsrfToken();
    return token ? { "X-CSRF-Token": token } : {};
  }

  async function request<T>(path: string, init: RequestInit): Promise<T> {
    const res = await fetch(`${baseUrl}${path}`, { credentials: "include", ...init });
    if (!res.ok) {
      throw await toHttpError(res);
    }
    if (res.status === 204) {
      return undefined as T;
    }
    const text = await res.text();
    if (!text) {
      return undefined as T;
    }
    return JSON.parse(text) as T;
  }

  function post<T>(path: string, body: unknown): Promise<T> {
    return request<T>(path, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...csrfHeaders() },
      body: JSON.stringify(body),
    });
  }

  function patch<T>(path: string, body: unknown): Promise<T> {
    return request<T>(path, {
      method: "PATCH",
      headers: { "Content-Type": "application/json", ...csrfHeaders() },
      body: JSON.stringify(body),
    });
  }

  function get<T>(path: string): Promise<T> {
    return request<T>(path, { method: "GET" });
  }

  interface VideoResponse {
    video_id: string;
    title?: string;
    status: string;
    failed_stage?: string | null;
    input_type?: string;
    source_url?: string | null;
    created_at?: string | null;
    signed_url?: string;
    upload_headers?: UploadHeaders;
  }

  interface VideoListResponse {
    items: VideoResponse[];
    next_cursor: string | null;
  }

  function mapVideo(res: VideoResponse, fallbackTitle: string): Video {
    return {
      id: res.video_id,
      title: res.title ?? fallbackTitle,
      status: (res.status as VideoStatus) ?? "PENDING",
      failedStage: res.failed_stage ?? undefined,
      inputType: (res.input_type as VideoInputType) ?? "LOCAL_FILE",
      sourceUrl: res.source_url ?? undefined,
      createdAt: res.created_at ?? new Date().toISOString(),
    };
  }

  function uploadSignedUrl(
    signedUrl: string,
    headers: UploadHeaders,
    file: File,
    onProgress?: (percent: number) => void
  ): Promise<void> {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("PUT", signedUrl);
      for (const [key, value] of Object.entries(headers)) {
        xhr.setRequestHeader(key, value);
      }
      xhr.upload.onprogress = (event) => {
        if (!event.lengthComputable || event.total === 0) return;
        onProgress?.(clampPercent(Math.round((event.loaded / event.total) * 100)));
      };
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          onProgress?.(100);
          resolve();
          return;
        }
        reject(new Error(`업로드 실패 (${xhr.status})`));
      };
      xhr.onerror = () => reject(new Error("업로드 실패"));
      xhr.onabort = () => reject(new Error("업로드가 취소되었습니다."));
      xhr.send(file);
    });
  }

  async function uploadFile(
    projectId: string,
    file: File,
    title: string,
    options?: UploadVideoOptions
  ): Promise<Video> {
    const encodedProjectId = encodeURIComponent(projectId);
    const created = await post<VideoResponse>(`/api/v1/projects/${encodedProjectId}/videos`, {
      input_type: "LOCAL_FILE",
      title,
      category: "GENERAL",
      extension: normalizedFileExtension(file.name),
    });
    options?.onUploadCreated?.(mapVideo(created, title));
    if (created.signed_url) {
      registerUploadWarning();
      try {
        await uploadSignedUrl(
          created.signed_url,
          created.upload_headers ?? {},
          file,
          options?.onProgress
        );
        const completed = await post<VideoResponse>(`/api/v1/videos/${created.video_id}/complete`, {});
        return mapVideo({ ...created, ...completed }, title);
      } finally {
        unregisterUploadWarning();
      }
    }
    const completed = await post<VideoResponse>(`/api/v1/videos/${created.video_id}/complete`, {});
    return mapVideo({ ...created, ...completed }, title);
  }

  async function uploadUrl(projectId: string, sourceUrl: string, title: string): Promise<Video> {
    const encodedProjectId = encodeURIComponent(projectId);
    const created = await post<VideoResponse>(`/api/v1/projects/${encodedProjectId}/videos`, {
      input_type: "EXTERNAL_URL",
      title,
      category: "GENERAL",
      source_url: sourceUrl,
    });
    return mapVideo(created, title);
  }

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

  interface SearchHistoryChunkResponse {
    ref: number;
    chunk_id: string;
    video_id: string;
    title: string;
    start_ms: number;
    end_ms: number;
    used: boolean;
  }

  interface SearchHistoryResponse {
    query: string;
    reqId: string;
    answer: string;
    chunks: SearchHistoryChunkResponse[];
  }

  function mapSearchChunk(c: SearchChunkResponse) {
    return {
      ref: c.ref,
      chunkId: c.chunk_id,
      videoId: c.video_id,
      title: c.title,
      startMs: c.start_ms,
      endMs: c.end_ms,
      text: c.text,
      used: c.used,
    };
  }

  function mapHistoryChunk(c: SearchHistoryChunkResponse) {
    return {
      ref: c.ref,
      chunkId: c.chunk_id,
      videoId: c.video_id,
      title: c.title,
      startMs: c.start_ms,
      endMs: c.end_ms,
      text: "",
      used: c.used,
    };
  }

  return {
    signup(req: SignupRequest): Promise<AuthResponse> {
      return post<AuthResponse>("/api/v1/auth/signup", req);
    },
    login(req: LoginRequest): Promise<AuthResponse> {
      return post<AuthResponse>("/api/v1/auth/login", req);
    },
    logout(): Promise<void> {
      return post<void>("/api/v1/auth/logout", {});
    },
    currentUser(): Promise<CurrentUserResponse> {
      return get<CurrentUserResponse>("/api/v1/auth/me");
    },
    listProjects(): Promise<Project[]> {
      return get<Project[]>("/api/v1/projects");
    },
    createProject(req: CreateProjectRequest): Promise<Project> {
      return post<Project>("/api/v1/projects", req);
    },
    renameProject(projectId: string, title: string): Promise<Project> {
      return patch<Project>(`/api/v1/projects/${encodeURIComponent(projectId)}`, { title });
    },
    deleteProject(projectId: string): Promise<void> {
      return request<void>(`/api/v1/projects/${encodeURIComponent(projectId)}`, {
        method: "DELETE",
        headers: csrfHeaders(),
      });
    },
    async listVideos(projectId: string): Promise<Video[]> {
      const res = await get<VideoListResponse>(
        `/api/v1/projects/${encodeURIComponent(projectId)}/videos`
      );
      return res.items.map((item) => mapVideo(item, item.title ?? ""));
    },
    async deleteVideos(videoIds: string[]): Promise<void> {
      await post<void>("/api/v1/videos:batch-delete", { video_ids: videoIds });
    },
    uploadVideo(
      projectId: string,
      input: UploadVideoInput,
      options?: UploadVideoOptions
    ): Promise<Video> {
      return input.kind === "file"
        ? uploadFile(projectId, input.file, input.title, options)
        : uploadUrl(projectId, input.sourceUrl, input.title);
    },
    async search(projectId: string, query: string): Promise<SearchResult> {
      const res = await post<SearchResponse>("/api/v1/search", {
        query,
        project_id: projectId,
      });
      return {
        reqId: res.req_id,
        answer: res.answer,
        chunks: res.chunks.map(mapSearchChunk),
      };
    },
    async getSearchHistory(projectId: string): Promise<SearchHistoryTurn[]> {
      const res = await get<SearchHistoryResponse[]>(
        `/api/v1/search/history?project_id=${encodeURIComponent(projectId)}`
      );
      return res.map((turn) => ({
        query: turn.query,
        result: {
          reqId: turn.reqId,
          answer: turn.answer,
          chunks: turn.chunks.map(mapHistoryChunk),
        },
      }));
    },
    async submitFeedback(reqId: string, rating: FeedbackRating): Promise<void> {
      await post<void>("/api/v1/feedbacks", { req_id: reqId, rating });
    },
    async getPlaybackUrl(videoId: string): Promise<string> {
      const res = await post<{ signed_url: string }>(`/api/v1/videos/${videoId}/playback-url`, {});
      return res.signed_url;
    },
  };
}
