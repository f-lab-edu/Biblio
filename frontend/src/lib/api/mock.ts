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

interface MockUser {
  userId: string;
  email: string;
  password: string;
}

const PROJECTS_KEY = "biblio.mock.projects";
const MOCK_CURRENT_USER_KEY = "biblio.mock.currentUserId";

function loadProjects(): Project[] {
  if (typeof window === "undefined") return [];
  const raw = window.localStorage.getItem(PROJECTS_KEY);
  return raw ? (JSON.parse(raw) as Project[]) : [];
}

function saveProjects(projects: Project[]): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(PROJECTS_KEY, JSON.stringify(projects));
}

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

export function createMockApi(): Api {
  const users = new Map<string, MockUser>();

  function setCurrentUser(userId: string): void {
    if (typeof window === "undefined") return;
    window.localStorage.setItem(MOCK_CURRENT_USER_KEY, userId);
  }

  function currentUserId(): string | null {
    if (typeof window === "undefined") return null;
    return window.localStorage.getItem(MOCK_CURRENT_USER_KEY);
  }

  function clearCurrentUser(): void {
    if (typeof window === "undefined") return;
    window.localStorage.removeItem(MOCK_CURRENT_USER_KEY);
  }

  function issueAuthResponse(user: MockUser): AuthResponse {
    setCurrentUser(user.userId);
    return { userId: user.userId, email: user.email };
  }

  return {
    async signup({ email, password }: SignupRequest): Promise<AuthResponse> {
      if (users.has(email)) {
        throw new Error("이미 가입된 이메일입니다.");
      }
      const userId = crypto.randomUUID();
      users.set(email, { userId, email, password });
      return issueAuthResponse({ userId, email, password });
    },

    async login({ email, password }: LoginRequest): Promise<AuthResponse> {
      const user = users.get(email);
      if (!user || user.password !== password) {
        throw new Error("이메일 또는 비밀번호가 올바르지 않습니다.");
      }
      return issueAuthResponse(user);
    },

    async logout(): Promise<void> {
      clearCurrentUser();
    },

    async currentUser(): Promise<{ userId: string }> {
      const userId = currentUserId();
      if (!userId) {
        throw new Error("로그인이 필요합니다.");
      }
      return { userId };
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

    async listVideos(projectId: string): Promise<Video[]> {
      const all = loadVideos();
      const mine = all.filter((v) => v.projectId === projectId);
      const upgraded = mine.map(toVideo);
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
  };
}
