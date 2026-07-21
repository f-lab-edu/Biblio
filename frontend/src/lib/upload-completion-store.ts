const UPLOAD_COMPLETIONS_KEY = "biblio.uploadCompletions";

interface UploadCompletionMarker {
  projectId: string;
  videoId: string;
}

function isUploadCompletionMarker(value: unknown): value is UploadCompletionMarker {
  if (typeof value !== "object" || value === null) return false;
  const marker = value as Record<string, unknown>;
  return typeof marker.projectId === "string" && typeof marker.videoId === "string";
}

function readMarkers(): UploadCompletionMarker[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(UPLOAD_COMPLETIONS_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter(isUploadCompletionMarker) : [];
  } catch {
    try {
      window.localStorage.removeItem(UPLOAD_COMPLETIONS_KEY);
    } catch {
      // 저장소가 차단된 경우 현재 화면의 상태만 사용한다.
    }
    return [];
  }
}

function writeMarkers(markers: UploadCompletionMarker[]): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(UPLOAD_COMPLETIONS_KEY, JSON.stringify(markers));
  } catch {
    // 저장소 문제로 실제 업로드를 중단하지 않는다.
  }
}

export function pendingUploadCompletionIds(projectId: string): string[] {
  return readMarkers()
    .filter((marker) => marker.projectId === projectId)
    .map((marker) => marker.videoId);
}

export function rememberUploadCompletion(projectId: string, videoId: string): void {
  const markers = readMarkers();
  if (markers.some((marker) => marker.projectId === projectId && marker.videoId === videoId)) {
    return;
  }
  writeMarkers([...markers, { projectId, videoId }]);
}

export function forgetUploadCompletion(projectId: string, videoId: string): void {
  writeMarkers(
    readMarkers().filter(
      (marker) => marker.projectId !== projectId || marker.videoId !== videoId
    )
  );
}
