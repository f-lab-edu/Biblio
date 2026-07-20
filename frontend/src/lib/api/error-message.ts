export type ErrorContext = "search" | "upload";

interface ErrorDetails {
  code?: string;
  status?: number;
  traceId?: string;
}

const USER_MESSAGES: Record<string, string> = {
  SEARCH_NOT_READY: "영상 처리가 끝난 뒤 검색할 수 있습니다.",
  NO_VIDEOS_UPLOADED: "먼저 검색할 영상을 등록해 주세요.",
  UNSUPPORTED_FILE_TYPE:
    "지원하지 않는 파일 형식입니다. MP4, WEBM, MOV, MKV, AVI, WMV 파일을 이용해 주세요.",
  FILE_TOO_LARGE: "파일은 최대 500MB까지 업로드할 수 있습니다.",
};

function errorDetails(error: unknown): ErrorDetails {
  if (typeof error === "string") return { code: error };
  if (typeof error !== "object" || error === null) return {};
  const value = error as Record<string, unknown>;
  return {
    code: typeof value.code === "string" ? value.code : undefined,
    status: typeof value.status === "number" ? value.status : undefined,
    traceId: typeof value.traceId === "string" ? value.traceId : undefined,
  };
}

export function userMessageFor(error: unknown, context: ErrorContext): string {
  const { code, status, traceId } = errorDetails(error);
  const knownMessage = code ? USER_MESSAGES[code] : undefined;
  if (knownMessage) return knownMessage;
  if (context === "search" && (code === "SERVICE_UNAVAILABLE" || status === 503)) {
    return "검색 요청이 몰리고 있습니다. 잠시 후 다시 시도해 주세요.";
  }
  return traceId ? `처리에 실패했습니다. 문의 코드: ${traceId}` : "처리에 실패했습니다.";
}
