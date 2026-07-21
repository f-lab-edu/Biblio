import { describe, expect, it } from "vitest";
import { userMessageFor, videoFailureMessage } from "@/lib/api/error-message";
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

describe("videoFailureMessage", () => {
  it.each([
    ["YOUTUBE_BLOCKED", "YouTube에서 영상 접근을 차단했습니다. 파일 업로드를 이용해 주세요."],
    ["SOURCE_UNAVAILABLE", "영상 원본을 불러올 수 없습니다."],
    [
      "SOURCE_LIMIT_EXCEEDED",
      "영상 원본이 처리 제한을 초과했습니다. 최대 길이는 60분, 최대 파일 크기는 500MB입니다.",
    ],
    ["AUDIO_EXTRACTION_FAILED", "영상에서 음성을 추출하지 못했습니다."],
    ["STT_FAILED", "음성 인식에 실패했습니다."],
    ["EMBEDDING_FAILED", "영상 내용을 검색 데이터로 변환하지 못했습니다."],
    ["INDEX_WRITE_FAILED", "검색 데이터 저장에 실패했습니다."],
    ["INTERNAL_PROCESSING_ERROR", "영상 처리 중 오류가 발생했습니다."],
  ])("maps %s to a stable message", (code, expected) => {
    expect(videoFailureMessage(code)).toBe(expected);
  });

  it("adds a trace id without exposing an exception message", () => {
    expect(videoFailureMessage("STT_FAILED", "trace-1")).toBe(
      "음성 인식에 실패했습니다. 문의 코드: trace-1"
    );
    expect(videoFailureMessage("UNKNOWN", "trace-2")).toBe(
      "영상 처리 중 오류가 발생했습니다. 문의 코드: trace-2"
    );
  });
});
