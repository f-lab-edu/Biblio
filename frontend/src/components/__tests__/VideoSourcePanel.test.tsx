import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const listVideos = vi.fn();
const uploadVideo = vi.fn();
const deleteVideos = vi.fn();
const completeVideo = vi.fn();
vi.mock("@/lib/api", () => ({
  api: {
    listVideos: (p: string) => listVideos(p),
    uploadVideo: (p: string, i: unknown, options?: unknown) => uploadVideo(p, i, options),
    deleteVideos: (ids: string[]) => deleteVideos(ids),
    completeVideo: (videoId: string) => completeVideo(videoId),
  },
}));

import { VideoSourcePanel } from "@/components/VideoSourcePanel";
import { HttpError } from "@/lib/api/http";
import { MAX_UPLOAD_SIZE_BYTES } from "@/lib/api/upload-constraints";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe("VideoSourcePanel", () => {
  beforeEach(() => {
    listVideos.mockReset();
    uploadVideo.mockReset();
    deleteVideos.mockReset();
    completeVideo.mockReset();
    localStorage.clear();
  });

  afterEach(() => vi.useRealTimers());

  it("lists videos with a status label", async () => {
    listVideos.mockResolvedValue([
      { id: "v1", title: "강의1", status: "PROCESSING", inputType: "EXTERNAL_URL", createdAt: "" },
      { id: "v2", title: "강의2", status: "READY", inputType: "LOCAL_FILE", createdAt: "" },
    ]);
    render(<VideoSourcePanel projectId="p1" />);
    expect(await screen.findByText("강의1")).toBeInTheDocument();
    expect(screen.getByText("처리중")).toBeInTheDocument();
    expect(screen.getByLabelText("처리 중")).toBeInTheDocument();
    expect(screen.getByText("완료")).toBeInTheDocument();
  });

  it("guides users to file upload when a download fails", async () => {
    listVideos.mockResolvedValue([
      {
        id: "v1",
        title: "다운로드 실패 영상",
        status: "FAILED",
        failedStage: "DOWNLOAD",
        inputType: "EXTERNAL_URL",
        createdAt: "",
      },
      {
        id: "v2",
        title: "다른 실패 영상",
        status: "FAILED",
        failedStage: "STT",
        inputType: "LOCAL_FILE",
        createdAt: "",
      },
    ]);

    render(<VideoSourcePanel projectId="p1" />);

    expect(
      await screen.findByText("다운로드 실패 · 파일 업로드를 이용해 주세요")
    ).toBeInTheDocument();
    expect(screen.getByText("실패")).toBeInTheDocument();
  });

  it("uploads a URL video and shows it", async () => {
    listVideos.mockResolvedValue([]);
    uploadVideo.mockResolvedValue({
      id: "v9",
      title: "새영상",
      status: "PROCESSING",
      inputType: "EXTERNAL_URL",
      createdAt: "",
    });
    render(<VideoSourcePanel projectId="p1" />);

    await userEvent.click(await screen.findByRole("button", { name: "URL" }));
    await userEvent.type(screen.getByLabelText("영상 제목"), "새영상");
    await userEvent.type(screen.getByLabelText("영상 URL"), "https://youtu.be/abc");
    await userEvent.click(screen.getByRole("button", { name: "업로드" }));

    expect(uploadVideo).toHaveBeenCalledWith("p1", {
      kind: "url",
      sourceUrl: "https://youtu.be/abc",
      title: "새영상",
    }, undefined);
    expect(await screen.findByText("새영상")).toBeInTheDocument();
  });

  it("shows local file upload progress and keeps one row when the list refreshes", async () => {
    listVideos.mockResolvedValue([]);
    let uploadOptions:
      | {
          onUploadCreated?: (video: unknown) => void;
          onProgress?: (percent: number) => void;
        }
      | undefined;
    let rejectUpload: (() => void) | undefined;
    uploadVideo.mockImplementation((_projectId, _input, options) => {
      uploadOptions = options;
      return new Promise((_resolve, reject) => {
        rejectUpload = () => reject(new Error("failed"));
      });
    });
    render(<VideoSourcePanel projectId="p1" />);

    const file = new File(["video"], "clip.mp4", { type: "video/mp4" });
    await userEvent.type(await screen.findByLabelText("영상 제목"), "로컬 영상");
    await userEvent.upload(screen.getByLabelText("영상 파일"), file);
    await userEvent.click(screen.getByRole("button", { name: "업로드" }));

    act(() => {
      uploadOptions?.onUploadCreated?.({
        id: "v3",
        title: "로컬 영상",
        status: "PENDING",
        inputType: "LOCAL_FILE",
        createdAt: "",
      });
      uploadOptions?.onProgress?.(42);
    });

    expect(await screen.findByText("로컬 영상")).toBeInTheDocument();
    expect(screen.getByText("업로드 중 42%")).toBeInTheDocument();
    expect(screen.getByLabelText("로컬 영상 선택")).toBeDisabled();
    expect(screen.getByRole("button", { name: "업로드" })).toBeEnabled();

    await userEvent.type(screen.getByLabelText("영상 제목"), "잘못된 파일");
    const userWithoutAcceptFilter = userEvent.setup({ applyAccept: false });
    await userWithoutAcceptFilter.upload(
      screen.getByLabelText("영상 파일"),
      new File(["bad"], "bad.exe", { type: "application/octet-stream" })
    );
    await userEvent.click(screen.getByRole("button", { name: "업로드" }));

    expect(uploadVideo).toHaveBeenCalledTimes(1);
    expect(screen.getByText("업로드 중 42%")).toBeInTheDocument();
    expect(
      screen.getByText(
        "지원하지 않는 파일 형식입니다. MP4, WEBM, MOV, MKV, AVI, WMV 파일을 이용해 주세요."
      )
    ).toBeInTheDocument();

    listVideos.mockResolvedValueOnce([
      { id: "v3", title: "로컬 영상", status: "PENDING", inputType: "LOCAL_FILE", createdAt: "" },
    ]);
    await userEvent.click(screen.getByRole("button", { name: "새로고침" }));

    expect(await screen.findByText("업로드 중 42%")).toBeInTheDocument();
    expect(screen.getAllByText("로컬 영상")).toHaveLength(1);

    await act(async () => {
      rejectUpload?.();
    });

    expect(await screen.findByText("업로드 실패")).toBeInTheDocument();
  });

  it("blocks a file larger than 500MiB before calling the API", async () => {
    listVideos.mockResolvedValue([]);
    render(<VideoSourcePanel projectId="p1" />);
    const file = new File(["video"], "large.mp4", { type: "video/mp4" });
    Object.defineProperty(file, "size", { value: MAX_UPLOAD_SIZE_BYTES + 1 });

    await userEvent.type(await screen.findByLabelText("영상 제목"), "큰 영상");
    await userEvent.upload(screen.getByLabelText("영상 파일"), file);
    await userEvent.click(screen.getByRole("button", { name: "업로드" }));

    expect(uploadVideo).not.toHaveBeenCalled();
    expect(screen.getByText("파일은 최대 500MB까지 업로드할 수 있습니다.")).toBeInTheDocument();
  });

  it("removes the local upload row when completion returns FILE_TOO_LARGE", async () => {
    listVideos.mockResolvedValue([]);
    uploadVideo.mockImplementation((_projectId, _input, options) => {
      options?.onUploadCreated?.({
        id: "v-large",
        title: "서버 확인 영상",
        status: "PENDING",
        inputType: "LOCAL_FILE",
        createdAt: "",
      });
      return Promise.reject(new HttpError("server detail", 400, "FILE_TOO_LARGE", "trace-large"));
    });
    render(<VideoSourcePanel projectId="p1" />);

    await userEvent.type(await screen.findByLabelText("영상 제목"), "서버 확인 영상");
    await userEvent.upload(
      screen.getByLabelText("영상 파일"),
      new File(["video"], "clip.mp4", { type: "video/mp4" })
    );
    await userEvent.click(screen.getByRole("button", { name: "업로드" }));

    expect(await screen.findByText("파일은 최대 500MB까지 업로드할 수 있습니다.")).toBeInTheDocument();
    expect(screen.queryByText("서버 확인 영상")).not.toBeInTheDocument();
  });

  it("blocks duplicate create calls but allows a later URL submission", async () => {
    listVideos.mockResolvedValue([]);
    const firstUpload = deferred<{ id: string; title: string; status: string; inputType: string; createdAt: string }>();
    uploadVideo
      .mockReturnValueOnce(firstUpload.promise)
      .mockResolvedValueOnce({
        id: "v-next",
        title: "다음 영상",
        status: "PENDING",
        inputType: "EXTERNAL_URL",
        createdAt: "",
      });
    render(<VideoSourcePanel projectId="p1" />);

    await userEvent.click(await screen.findByRole("button", { name: "URL" }));
    await userEvent.type(screen.getByLabelText("영상 제목"), "첫 영상");
    await userEvent.type(screen.getByLabelText("영상 URL"), "https://youtu.be/first");
    await userEvent.click(screen.getByRole("button", { name: "업로드" }));

    expect(screen.getByRole("button", { name: "업로드" })).toBeDisabled();
    expect(uploadVideo).toHaveBeenCalledTimes(1);
    await act(async () => {
      firstUpload.resolve({
        id: "v-first",
        title: "첫 영상",
        status: "PENDING",
        inputType: "EXTERNAL_URL",
        createdAt: "",
      });
    });
    expect(await screen.findByText("첫 영상")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "업로드" })).toBeEnabled();

    await userEvent.type(screen.getByLabelText("영상 제목"), "다음 영상");
    await userEvent.type(screen.getByLabelText("영상 URL"), "https://youtu.be/next");
    await userEvent.click(screen.getByRole("button", { name: "업로드" }));

    expect(uploadVideo).toHaveBeenCalledTimes(2);
    expect(await screen.findByText("다음 영상")).toBeInTheDocument();
  });

  it("does not upload a non-YouTube URL", async () => {
    listVideos.mockResolvedValue([]);
    render(<VideoSourcePanel projectId="p1" />);

    await userEvent.click(await screen.findByRole("button", { name: "URL" }));
    await userEvent.type(screen.getByLabelText("영상 제목"), "새영상");
    await userEvent.type(screen.getByLabelText("영상 URL"), "https://example.com/watch?v=1");
    await userEvent.click(screen.getByRole("button", { name: "업로드" }));

    expect(uploadVideo).not.toHaveBeenCalled();
    expect(screen.getByText("YouTube URL만 등록할 수 있습니다.")).toBeInTheDocument();
  });

  it("deletes selected videos", async () => {
    listVideos.mockResolvedValue([
      { id: "v1", title: "강의1", status: "READY", inputType: "LOCAL_FILE", createdAt: "" },
      { id: "v2", title: "강의2", status: "READY", inputType: "LOCAL_FILE", createdAt: "" },
    ]);
    deleteVideos.mockResolvedValue(undefined);
    render(<VideoSourcePanel projectId="p1" />);

    await userEvent.click(await screen.findByLabelText("강의1 선택"));
    await userEvent.click(screen.getByRole("button", { name: "삭제" }));

    expect(deleteVideos).toHaveBeenCalledWith(["v1"]);
    expect(screen.queryByText("강의1")).not.toBeInTheDocument();
    expect(screen.getByText("강의2")).toBeInTheDocument();
  });

  it("polls active videos and shows the final failure message", async () => {
    vi.useFakeTimers();
    listVideos
      .mockResolvedValueOnce([
        { id: "v1", title: "강의1", status: "PROCESSING", inputType: "LOCAL_FILE", createdAt: "" },
      ])
      .mockResolvedValueOnce([
        {
          id: "v1",
          title: "강의1",
          status: "FAILED",
          failedStage: "STT",
          failureCode: "STT_FAILED",
          failureTraceId: "trace-stt",
          inputType: "LOCAL_FILE",
          createdAt: "",
        },
      ]);

    render(<VideoSourcePanel projectId="p1" />);
    await act(async () => vi.advanceTimersByTimeAsync(0));
    expect(screen.getByText("처리중")).toBeInTheDocument();

    await act(async () => vi.advanceTimersByTimeAsync(5000));

    expect(listVideos).toHaveBeenCalledTimes(2);
    expect(
      screen.getByText("음성 인식에 실패했습니다. 문의 코드: trace-stt")
    ).toBeInTheDocument();

    await act(async () => vi.advanceTimersByTimeAsync(10_000));
    expect(listVideos).toHaveBeenCalledTimes(2);
  });

  it("does not poll when every video is terminal", async () => {
    vi.useFakeTimers();
    listVideos.mockResolvedValue([
      { id: "v1", title: "강의1", status: "READY", inputType: "LOCAL_FILE", createdAt: "" },
    ]);

    render(<VideoSourcePanel projectId="p1" />);
    await act(async () => vi.advanceTimersByTimeAsync(0));
    await act(async () => vi.advanceTimersByTimeAsync(15_000));

    expect(listVideos).toHaveBeenCalledTimes(1);
  });

  it("keeps an uploaded URL video when an older list response arrives", async () => {
    const initialList = deferred<unknown[]>();
    listVideos.mockReturnValue(initialList.promise);
    uploadVideo.mockResolvedValue({
      id: "v-new",
      title: "새 영상",
      status: "PENDING",
      inputType: "EXTERNAL_URL",
      createdAt: "",
    });
    render(<VideoSourcePanel projectId="p1" />);
    await waitFor(() => expect(listVideos).toHaveBeenCalledTimes(1));

    await userEvent.click(screen.getByRole("button", { name: "URL" }));
    await userEvent.type(screen.getByLabelText("영상 제목"), "새 영상");
    await userEvent.type(screen.getByLabelText("영상 URL"), "https://youtu.be/new");
    await userEvent.click(screen.getByRole("button", { name: "업로드" }));
    expect(await screen.findByText("새 영상")).toBeInTheDocument();

    await act(async () => initialList.resolve([]));

    expect(screen.getByText("새 영상")).toBeInTheDocument();
  });

  it("keeps checking after PUT succeeds and complete is temporarily unavailable", async () => {
    const completion = deferred<{ id: string; status: string }>();
    listVideos.mockResolvedValue([]);
    completeVideo.mockReturnValue(completion.promise);
    uploadVideo.mockImplementation((_projectId, _input, options) => {
      const created = {
        id: "v-check",
        title: "확인 영상",
        status: "PENDING",
        inputType: "LOCAL_FILE",
        createdAt: "",
      };
      options?.onUploadCreated?.(created);
      options?.onUploadTransferred?.(created);
      return Promise.reject(new Error("network unavailable"));
    });
    render(<VideoSourcePanel projectId="p1" />);

    await userEvent.type(await screen.findByLabelText("영상 제목"), "확인 영상");
    await userEvent.upload(
      screen.getByLabelText("영상 파일"),
      new File(["video"], "clip.mp4", { type: "video/mp4" })
    );
    await userEvent.click(screen.getByRole("button", { name: "업로드" }));

    expect(await screen.findByText("업로드 완료 확인 중")).toBeInTheDocument();
    expect(completeVideo).toHaveBeenCalledWith("v-check");

    await act(async () => completion.resolve({ id: "v-check", status: "UPLOADED" }));
    expect(await screen.findByText("업로드됨")).toBeInTheDocument();
  });

  it("recovers a stored completion marker after remount", async () => {
    localStorage.setItem(
      "biblio.uploadCompletions",
      JSON.stringify([{ projectId: "p1", videoId: "v-stored" }])
    );
    listVideos.mockResolvedValue([]);
    completeVideo.mockResolvedValue({ id: "v-stored", status: "UPLOADED" });

    render(<VideoSourcePanel projectId="p1" />);

    await waitFor(() => expect(completeVideo).toHaveBeenCalledWith("v-stored"));
    expect(localStorage.getItem("biblio.uploadCompletions")).toBe("[]");
  });

  it("retries a stored completion marker when the first recovery is temporarily unavailable", async () => {
    vi.useFakeTimers();
    localStorage.setItem(
      "biblio.uploadCompletions",
      JSON.stringify([{ projectId: "p1", videoId: "v-stored" }])
    );
    listVideos.mockResolvedValue([]);
    completeVideo
      .mockRejectedValueOnce(new Error("temporary"))
      .mockResolvedValueOnce({ id: "v-stored", status: "UPLOADED" });

    render(<VideoSourcePanel projectId="p1" />);
    await act(async () => vi.advanceTimersByTimeAsync(0));
    expect(completeVideo).toHaveBeenCalledTimes(1);

    await act(async () => vi.advanceTimersByTimeAsync(5000));

    expect(completeVideo).toHaveBeenCalledTimes(2);
    expect(localStorage.getItem("biblio.uploadCompletions")).toBe("[]");
  });

  it("does not restore a deleted video when stored completion recovery finishes late", async () => {
    const completion = deferred<{ id: string; status: string }>();
    localStorage.setItem(
      "biblio.uploadCompletions",
      JSON.stringify([{ projectId: "p1", videoId: "v-stored" }])
    );
    listVideos.mockResolvedValue([
      {
        id: "v-stored",
        title: "삭제할 영상",
        status: "UPLOADED",
        inputType: "LOCAL_FILE",
        createdAt: "",
      },
    ]);
    completeVideo.mockReturnValue(completion.promise);
    deleteVideos.mockResolvedValue(undefined);

    render(<VideoSourcePanel projectId="p1" />);
    await waitFor(() => expect(completeVideo).toHaveBeenCalledWith("v-stored"));
    await userEvent.click(await screen.findByLabelText("삭제할 영상 선택"));
    await userEvent.click(screen.getByRole("button", { name: "삭제" }));
    expect(screen.queryByText("삭제할 영상")).not.toBeInTheDocument();

    await act(async () => completion.resolve({ id: "v-stored", status: "UPLOADED" }));

    expect(screen.queryByText("삭제할 영상")).not.toBeInTheDocument();
    expect(localStorage.getItem("biblio.uploadCompletions")).toBe("[]");
  });

  it("keeps the current list and action error while a poll fails, then retries", async () => {
    vi.useFakeTimers();
    listVideos
      .mockResolvedValueOnce([
        { id: "v1", title: "강의1", status: "PROCESSING", inputType: "LOCAL_FILE", createdAt: "" },
      ])
      .mockRejectedValueOnce(new Error("temporary"))
      .mockResolvedValueOnce([
        { id: "v1", title: "강의1", status: "READY", inputType: "LOCAL_FILE", createdAt: "" },
      ]);
    render(<VideoSourcePanel projectId="p1" />);
    await act(async () => vi.advanceTimersByTimeAsync(0));

    fireEvent.click(screen.getByRole("button", { name: "URL" }));
    fireEvent.change(screen.getByLabelText("영상 제목"), { target: { value: "잘못된 URL" } });
    fireEvent.change(screen.getByLabelText("영상 URL"), {
      target: { value: "https://example.com/video" },
    });
    fireEvent.click(screen.getByRole("button", { name: "업로드" }));
    expect(screen.getByText("YouTube URL만 등록할 수 있습니다.")).toBeInTheDocument();

    await act(async () => vi.advanceTimersByTimeAsync(5000));
    expect(screen.getByText("강의1")).toBeInTheDocument();
    expect(screen.getByText("처리중")).toBeInTheDocument();
    expect(
      screen.getByText("영상 상태를 갱신하지 못했습니다. 잠시 후 다시 시도합니다.")
    ).toBeInTheDocument();
    expect(screen.getByText("YouTube URL만 등록할 수 있습니다.")).toBeInTheDocument();

    await act(async () => vi.advanceTimersByTimeAsync(5000));
    expect(screen.getByText("완료")).toBeInTheDocument();
    expect(
      screen.queryByText("영상 상태를 갱신하지 못했습니다. 잠시 후 다시 시도합니다.")
    ).not.toBeInTheDocument();
  });

  it("refreshes immediately when a background tab becomes visible", async () => {
    vi.useFakeTimers();
    listVideos.mockResolvedValue([
      { id: "v1", title: "강의1", status: "PROCESSING", inputType: "LOCAL_FILE", createdAt: "" },
    ]);
    Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
    render(<VideoSourcePanel projectId="p1" />);
    await act(async () => vi.advanceTimersByTimeAsync(0));

    await act(async () => document.dispatchEvent(new Event("visibilitychange")));

    expect(listVideos).toHaveBeenCalledTimes(2);
    await act(async () => vi.advanceTimersByTimeAsync(4999));
    expect(listVideos).toHaveBeenCalledTimes(2);
  });

  it("cleans up polling when the component unmounts", async () => {
    vi.useFakeTimers();
    listVideos.mockResolvedValue([
      { id: "v1", title: "강의1", status: "PROCESSING", inputType: "LOCAL_FILE", createdAt: "" },
    ]);
    const { unmount } = render(<VideoSourcePanel projectId="p1" />);
    await act(async () => vi.advanceTimersByTimeAsync(0));

    unmount();
    await act(async () => vi.advanceTimersByTimeAsync(10_000));

    expect(listVideos).toHaveBeenCalledTimes(1);
  });
});
