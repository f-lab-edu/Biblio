export const SUPPORTED_VIDEO_EXTENSIONS = [".mp4", ".webm", ".mov", ".mkv", ".avi", ".wmv"] as const;
export const MAX_UPLOAD_SIZE_BYTES = 500 * 1024 * 1024;

export type UploadConstraintCode = "UNSUPPORTED_FILE_TYPE" | "FILE_TOO_LARGE";

export type UploadFileValidation =
  | { ok: true; extension: string }
  | { ok: false; code: UploadConstraintCode };

export function normalizedFileExtension(fileName: string): string {
  const dot = fileName.lastIndexOf(".");
  return dot >= 0 ? fileName.slice(dot).toLowerCase() : "";
}

export function validateUploadFile(file: File): UploadFileValidation {
  const extension = normalizedFileExtension(file.name);
  if (!SUPPORTED_VIDEO_EXTENSIONS.some((supported) => supported === extension)) {
    return { ok: false, code: "UNSUPPORTED_FILE_TYPE" };
  }
  if (file.size > MAX_UPLOAD_SIZE_BYTES) {
    return { ok: false, code: "FILE_TOO_LARGE" };
  }
  return { ok: true, extension };
}
