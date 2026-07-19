import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const search = vi.fn();
const getSearchHistory = vi.fn();
const submitFeedback = vi.fn();
vi.mock("@/lib/api", () => ({
  api: {
    search: (p: string, q: string) => search(p, q),
    getSearchHistory: (p: string) => getSearchHistory(p),
    submitFeedback: (reqId: string, rating: "LIKE" | "DISLIKE") =>
      submitFeedback(reqId, rating),
  },
}));

import { SearchPanel } from "@/components/SearchPanel";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe("SearchPanel", () => {
  beforeEach(() => {
    search.mockReset();
    getSearchHistory.mockReset();
    submitFeedback.mockReset();
    getSearchHistory.mockResolvedValue([]);
    submitFeedback.mockResolvedValue(undefined);
  });

  it("runs a search and shows the answer and a citation", async () => {
    search.mockResolvedValue({
      reqId: "r1",
      answer: "이것이 답변입니다",
      chunks: [
        { ref: 1, chunkId: "c1", videoId: "v1", title: "강의1", startMs: 30000, endMs: 45000, text: "조각", used: true },
      ],
    });
    const onPlay = vi.fn();
    render(<SearchPanel projectId="p1" onPlay={onPlay} />);

    await userEvent.type(screen.getByLabelText("검색어"), "임베딩이 뭐야");
    await userEvent.click(screen.getByRole("button", { name: "검색" }));

    expect(search).toHaveBeenCalledWith("p1", "임베딩이 뭐야");
    expect(await screen.findByText("이것이 답변입니다")).toBeInTheDocument();

    const citation = await screen.findByRole("button", { name: /강의1/ });
    await userEvent.click(citation);
    expect(onPlay).toHaveBeenCalledWith(
      expect.objectContaining({ videoId: "v1", startMs: 30000 })
    );
  });

  it("shows a retry message when search capacity is temporarily full", async () => {
    search.mockRejectedValue(Object.assign(new Error("요청 실패 (503)"), { status: 503 }));
    render(<SearchPanel projectId="p1" onPlay={vi.fn()} />);

    await userEvent.type(screen.getByLabelText("검색어"), "임베딩이 뭐야");
    await userEvent.click(screen.getByRole("button", { name: "검색" }));

    expect(
      await screen.findByText("검색 요청이 몰리고 있습니다. 잠시 후 다시 시도해 주세요.")
    ).toBeInTheDocument();
  });

  it("restores history on project entry and keeps live search append", async () => {
    const onPlay = vi.fn();
    getSearchHistory.mockResolvedValue([
      {
        query: "이전 질문",
        result: {
          reqId: "old-r1",
          answer: "이전 답변",
          chunks: [
            {
              ref: 1,
              chunkId: "old-c1",
              videoId: "old-v1",
              title: "이전 강의",
              startMs: 61000,
              endMs: 70000,
              text: "",
              used: true,
            },
          ],
        },
      },
    ]);
    search.mockResolvedValue({
      reqId: "new-r1",
      answer: "새 답변",
      chunks: [],
    });
    render(<SearchPanel projectId="p1" onPlay={onPlay} />);

    expect(getSearchHistory).toHaveBeenCalledWith("p1");
    expect(await screen.findByText("이전 질문")).toBeInTheDocument();
    expect(screen.getByText("이전 답변")).toBeInTheDocument();
    const restoredCitation = screen.getByRole("button", { name: /이전 강의/ });
    await userEvent.click(restoredCitation);
    expect(onPlay).toHaveBeenCalledWith(
      expect.objectContaining({ videoId: "old-v1", startMs: 61000 })
    );

    await userEvent.type(screen.getByLabelText("검색어"), "새 질문");
    await userEvent.click(screen.getByRole("button", { name: "검색" }));

    expect(search).toHaveBeenCalledWith("p1", "새 질문");
    expect(await screen.findByText("새 답변")).toBeInTheDocument();
    expect(screen.getByText("이전 답변")).toBeInTheDocument();
  });

  it("does not let a late history response remove a live search turn", async () => {
    const history = deferred<
      {
        query: string;
        result: { reqId: string; answer: string; chunks: [] };
      }[]
    >();
    getSearchHistory.mockReturnValue(history.promise);
    search.mockResolvedValue({
      reqId: "new-r1",
      answer: "새 답변",
      chunks: [],
    });
    render(<SearchPanel projectId="p1" onPlay={vi.fn()} />);

    await userEvent.type(screen.getByLabelText("검색어"), "새 질문");
    await userEvent.click(screen.getByRole("button", { name: "검색" }));
    expect(await screen.findByText("새 답변")).toBeInTheDocument();

    history.resolve([
      {
        query: "이전 질문",
        result: { reqId: "old-r1", answer: "이전 답변", chunks: [] },
      },
    ]);

    expect(await screen.findByText("이전 답변")).toBeInTheDocument();
    expect(screen.getByText("새 답변")).toBeInTheDocument();
  });

  it("keeps a live search turn when history loading fails late", async () => {
    const history = deferred<
      {
        query: string;
        result: { reqId: string; answer: string; chunks: [] };
      }[]
    >();
    getSearchHistory.mockReturnValue(history.promise);
    search.mockResolvedValue({
      reqId: "new-r1",
      answer: "새 답변",
      chunks: [
        {
          ref: 1,
          chunkId: "new-c1",
          videoId: "new-v1",
          title: "새 강의",
          startMs: 1000,
          endMs: 2000,
          text: "조각",
          used: true,
        },
      ],
    });
    render(<SearchPanel projectId="p1" onPlay={vi.fn()} />);

    await userEvent.type(screen.getByLabelText("검색어"), "새 질문");
    await userEvent.click(screen.getByRole("button", { name: "검색" }));
    expect(await screen.findByText("새 답변")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "좋아요" })).toBeInTheDocument();

    await act(async () => {
      history.reject(new Error("history failed"));
      await history.promise.catch(() => undefined);
    });

    expect(screen.getByText("새 답변")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "좋아요" })).toBeInTheDocument();
  });

  it("submits feedback for a live turn with the result reqId", async () => {
    search.mockResolvedValue({
      reqId: "server-req-1",
      answer: "라이브 답변",
      chunks: [
        {
          ref: 1,
          chunkId: "c1",
          videoId: "v1",
          title: "강의1",
          startMs: 30000,
          endMs: 45000,
          text: "조각",
          used: true,
        },
      ],
    });
    render(<SearchPanel projectId="p1" onPlay={vi.fn()} />);

    await userEvent.type(screen.getByLabelText("검색어"), "라이브 질문");
    await userEvent.click(screen.getByRole("button", { name: "검색" }));
    expect(await screen.findByText("라이브 답변")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "좋아요" }));

    expect(submitFeedback).toHaveBeenCalledWith("server-req-1", "LIKE");
    expect(screen.getByRole("button", { name: "좋아요" })).toHaveAttribute(
      "aria-pressed",
      "true"
    );
  });

  it("does not mark feedback as selected when submit fails", async () => {
    submitFeedback.mockRejectedValue(new Error("boom"));
    getSearchHistory.mockResolvedValue([
      {
        query: "이전 질문",
        result: {
          reqId: "old-r1",
          answer: "이전 답변",
          chunks: [
            {
              ref: 1,
              chunkId: "old-c1",
              videoId: "old-v1",
              title: "이전 강의",
              startMs: 61000,
              endMs: 70000,
              text: "",
              used: true,
            },
          ],
        },
      },
    ]);
    render(<SearchPanel projectId="p1" onPlay={vi.fn()} />);

    expect(await screen.findByText("이전 답변")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "좋아요" }));

    expect(screen.getByRole("button", { name: "좋아요" })).toHaveAttribute(
      "aria-pressed",
      "false"
    );
    expect(await screen.findByText("피드백 전송에 실패했습니다.")).toBeInTheDocument();
  });

  it("shows a specific message for 404 feedback failures", async () => {
    submitFeedback.mockRejectedValue(Object.assign(new Error("요청 실패 (404)"), { status: 404 }));
    getSearchHistory.mockResolvedValue([
      {
        query: "이전 질문",
        result: {
          reqId: "old-r1",
          answer: "이전 답변",
          chunks: [
            {
              ref: 1,
              chunkId: "old-c1",
              videoId: "old-v1",
              title: "이전 강의",
              startMs: 61000,
              endMs: 70000,
              text: "",
              used: true,
            },
          ],
        },
      },
    ]);
    render(<SearchPanel projectId="p1" onPlay={vi.fn()} />);

    expect(await screen.findByText("이전 답변")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "싫어요" }));

    expect(
      await screen.findByText("이 답변에는 지금 피드백을 남길 수 없습니다.")
    ).toBeInTheDocument();
  });

  it("resubmits for a different rating but not for the same rating", async () => {
    getSearchHistory.mockResolvedValue([
      {
        query: "이전 질문",
        result: {
          reqId: "old-r1",
          answer: "이전 답변",
          chunks: [
            {
              ref: 1,
              chunkId: "old-c1",
              videoId: "old-v1",
              title: "이전 강의",
              startMs: 61000,
              endMs: 70000,
              text: "",
              used: true,
            },
          ],
        },
      },
    ]);
    render(<SearchPanel projectId="p1" onPlay={vi.fn()} />);

    expect(await screen.findByText("이전 답변")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "좋아요" }));
    await userEvent.click(screen.getByRole("button", { name: "좋아요" }));
    await userEvent.click(screen.getByRole("button", { name: "싫어요" }));

    expect(submitFeedback).toHaveBeenCalledTimes(2);
    expect(submitFeedback).toHaveBeenNthCalledWith(1, "old-r1", "LIKE");
    expect(submitFeedback).toHaveBeenNthCalledWith(2, "old-r1", "DISLIKE");
  });

  it("keeps the previous rating selected when rating change fails", async () => {
    submitFeedback.mockResolvedValueOnce(undefined).mockRejectedValueOnce(new Error("boom"));
    getSearchHistory.mockResolvedValue([
      {
        query: "이전 질문",
        result: {
          reqId: "old-r1",
          answer: "이전 답변",
          chunks: [
            {
              ref: 1,
              chunkId: "old-c1",
              videoId: "old-v1",
              title: "이전 강의",
              startMs: 61000,
              endMs: 70000,
              text: "",
              used: true,
            },
          ],
        },
      },
    ]);
    render(<SearchPanel projectId="p1" onPlay={vi.fn()} />);

    expect(await screen.findByText("이전 답변")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "좋아요" }));
    await userEvent.click(screen.getByRole("button", { name: "싫어요" }));

    expect(screen.getByRole("button", { name: "좋아요" })).toHaveAttribute(
      "aria-pressed",
      "true"
    );
    expect(screen.getByRole("button", { name: "싫어요" })).toHaveAttribute(
      "aria-pressed",
      "false"
    );
  });

  it("does not send duplicate feedback while a request is pending", async () => {
    const pending = deferred<void>();
    submitFeedback.mockReturnValue(pending.promise);
    getSearchHistory.mockResolvedValue([
      {
        query: "이전 질문",
        result: {
          reqId: "old-r1",
          answer: "이전 답변",
          chunks: [
            {
              ref: 1,
              chunkId: "old-c1",
              videoId: "old-v1",
              title: "이전 강의",
              startMs: 61000,
              endMs: 70000,
              text: "",
              used: true,
            },
          ],
        },
      },
    ]);
    render(<SearchPanel projectId="p1" onPlay={vi.fn()} />);

    expect(await screen.findByText("이전 답변")).toBeInTheDocument();
    const like = screen.getByRole("button", { name: "좋아요" });
    await userEvent.click(like);
    await userEvent.click(like);

    expect(submitFeedback).toHaveBeenCalledTimes(1);
    pending.resolve();
  });

  it("does not show feedback buttons for answers without chunks", async () => {
    search.mockResolvedValue({
      reqId: "empty-r1",
      answer: "검색 결과가 없습니다",
      chunks: [],
    });
    render(<SearchPanel projectId="p1" onPlay={vi.fn()} />);

    await userEvent.type(screen.getByLabelText("검색어"), "없는 질문");
    await userEvent.click(screen.getByRole("button", { name: "검색" }));

    expect(await screen.findByText("검색 결과가 없습니다")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "좋아요" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "싫어요" })).not.toBeInTheDocument();
  });

  it("submits feedback for restored history turns", async () => {
    getSearchHistory.mockResolvedValue([
      {
        query: "이전 질문",
        result: {
          reqId: "old-r1",
          answer: "이전 답변",
          chunks: [
            {
              ref: 1,
              chunkId: "old-c1",
              videoId: "old-v1",
              title: "이전 강의",
              startMs: 61000,
              endMs: 70000,
              text: "",
              used: true,
            },
          ],
        },
      },
    ]);
    render(<SearchPanel projectId="p1" onPlay={vi.fn()} />);

    expect(await screen.findByText("이전 답변")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "싫어요" }));

    expect(submitFeedback).toHaveBeenCalledWith("old-r1", "DISLIKE");
  });
});
