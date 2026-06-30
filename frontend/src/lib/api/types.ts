export interface SignupRequest {
  email: string;
  password: string;
}

export interface LoginRequest {
  email: string;
  password: string;
}

export interface AuthResponse {
  userId: string;
  email: string;
}

export interface CurrentUserResponse {
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

export interface Api {
  signup(req: SignupRequest): Promise<AuthResponse>;
  login(req: LoginRequest): Promise<AuthResponse>;
  logout(): Promise<void>;
  currentUser(): Promise<CurrentUserResponse>;
  listProjects(): Promise<Project[]>;
  createProject(req: CreateProjectRequest): Promise<Project>;
  listVideos(projectId: string): Promise<Video[]>;
  uploadVideo(projectId: string, input: UploadVideoInput): Promise<Video>;
  search(projectId: string, query: string): Promise<SearchResult>;
  getPlaybackUrl(videoId: string): Promise<string>;
}
