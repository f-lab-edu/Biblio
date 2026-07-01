import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const listVideos = vi.fn();
const uploadVideo = vi.fn();
const deleteVideos = vi.fn();
vi.mock("@/lib/api", () => ({
  api: {
    listVideos: (p: string) => listVideos(p),
    uploadVideo: (p: string, i: unknown) => uploadVideo(p, i),
    deleteVideos: (ids: string[]) => deleteVideos(ids),
  },
}));

import { VideoSourcePanel } from "@/components/VideoSourcePanel";

describe("VideoSourcePanel", () => {
  beforeEach(() => {
    listVideos.mockReset();
    uploadVideo.mockReset();
    deleteVideos.mockReset();
  });

  it("lists videos with a status label", async () => {
    listVideos.mockResolvedValue([
      { id: "v1", title: "강의1", status: "PROCESSING", inputType: "EXTERNAL_URL", createdAt: "" },
      { id: "v2", title: "강의2", status: "READY", inputType: "LOCAL_FILE", createdAt: "" },
    ]);
    render(<VideoSourcePanel projectId="p1" />);
    expect(await screen.findByText("강의1")).toBeInTheDocument();
    expect(screen.getByText("처리중")).toBeInTheDocument();
    expect(screen.getByText("완료")).toBeInTheDocument();
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
    });
    expect(await screen.findByText("새영상")).toBeInTheDocument();
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
});
