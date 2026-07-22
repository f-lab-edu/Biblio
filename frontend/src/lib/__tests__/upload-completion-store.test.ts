import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  forgetUploadCompletion,
  pendingUploadCompletionIds,
  rememberUploadCompletion,
} from "@/lib/upload-completion-store";

describe("upload completion store", () => {
  beforeEach(() => localStorage.clear());

  it("stores unique video ids by project and removes one marker", () => {
    rememberUploadCompletion("p1", "v1");
    rememberUploadCompletion("p1", "v1");
    rememberUploadCompletion("p2", "v2");

    expect(pendingUploadCompletionIds("p1")).toEqual(["v1"]);
    expect(pendingUploadCompletionIds("p2")).toEqual(["v2"]);

    forgetUploadCompletion("p1", "v1");
    expect(pendingUploadCompletionIds("p1")).toEqual([]);
    expect(pendingUploadCompletionIds("p2")).toEqual(["v2"]);
  });

  it("falls back to an empty list for corrupted storage", () => {
    localStorage.setItem("biblio.uploadCompletions", "not-json");

    expect(pendingUploadCompletionIds("p1")).toEqual([]);
    expect(localStorage.getItem("biblio.uploadCompletions")).toBeNull();
  });

  it("does not throw when localStorage writes are blocked", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });

    expect(() => rememberUploadCompletion("p1", "v1")).not.toThrow();
  });
});
