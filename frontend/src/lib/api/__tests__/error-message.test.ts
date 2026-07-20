import { describe, expect, it } from "vitest";
import { userMessageFor } from "@/lib/api/error-message";
import { HttpError } from "@/lib/api/http";

describe("userMessageFor", () => {
  it.each([
    ["SEARCH_NOT_READY", "영상 처리가 끝난 뒤 검색할 수 있습니다."],
    ["NO_VIDEOS_UPLOADED", "먼저 검색할 영상을 등록해 주세요."],
    ["UNSUPPORTED_FILE_TYPE", "지원하지 않는 파일 형식입니다. MP4, WEBM, MOV, MKV, AVI, WMV 파일을 이용해 주세요."],
    ["FILE_TOO_LARGE", "파일은 최대 500MB까지 업로드할 수 있습니다."],
  ])("maps %s to a stable user message", (code, expected) => {
    expect(userMessageFor(new HttpError("internal", 400, code), "upload")).toBe(expected);
  });

  it("keeps the search capacity message for a bodyless 503", () => {
    expect(userMessageFor(new HttpError("request failed", 503), "search")).toBe(
      "검색 요청이 몰리고 있습니다. 잠시 후 다시 시도해 주세요."
    );
  });

  it("shows only the trace id for an unknown server error", () => {
    expect(userMessageFor(new HttpError("database detail", 500, "UNKNOWN", "trace-1"), "upload")).toBe(
      "처리에 실패했습니다. 문의 코드: trace-1"
    );
  });
});
