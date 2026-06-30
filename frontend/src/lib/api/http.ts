import type {
  Api,
  AuthResponse,
  CreateProjectRequest,
  CurrentUserResponse,
  LoginRequest,
  Project,
  SearchResult,
  SignupRequest,
  UploadVideoInput,
  Video,
  VideoInputType,
  VideoStatus,
} from "./types";
import { getCsrfToken } from "@/lib/auth/token";

export function createHttpApi(baseUrl: string): Api {
  function csrfHeaders(): Record<string, string> {
    const token = getCsrfToken();
    return token ? { "X-CSRF-Token": token } : {};
  }

  async function request<T>(path: string, init: RequestInit): Promise<T> {
    const res = await fetch(`${baseUrl}${path}`, { credentials: "include", ...init });
    if (!res.ok) {
      throw new Error(`요청 실패 (${res.status})`);
    }
    if (res.status === 204) {
      return undefined as T;
    }
    return (await res.json()) as T;
  }

  function post<T>(path: string, body: unknown): Promise<T> {
    return request<T>(path, {
      method: "POST",
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
    const created = await post<VideoResponse>(`/api/v1/projects/${projectId}/videos`, {
      input_type: "LOCAL_FILE",
      title,
      category: "GENERAL",
      extension: fileExtension(file.name),
    });
    if (created.signed_url) {
      const put = await fetch(created.signed_url, { method: "PUT", body: file });
      if (!put.ok) throw new Error(`업로드 실패 (${put.status})`);
    }
    const completed = await post<VideoResponse>(`/api/v1/videos/${created.video_id}/complete`, {});
    return mapVideo({ ...created, ...completed }, title);
  }

  async function uploadUrl(projectId: string, sourceUrl: string, title: string): Promise<Video> {
    const created = await post<VideoResponse>(`/api/v1/projects/${projectId}/videos`, {
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
    async listVideos(projectId: string): Promise<Video[]> {
      const items = await get<VideoResponse[]>(`/api/v1/videos?project_id=${projectId}`);
      return items.map((item) => mapVideo(item, item.title ?? ""));
    },
    uploadVideo(projectId: string, input: UploadVideoInput): Promise<Video> {
      return input.kind === "file"
        ? uploadFile(projectId, input.file, input.title)
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
      const res = await post<{ signed_url: string }>(`/api/v1/videos/${videoId}/playback-url`, {});
      return res.signed_url;
    },
  };
}
