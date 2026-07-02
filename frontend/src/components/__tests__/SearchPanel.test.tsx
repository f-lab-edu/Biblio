import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const search = vi.fn();
const getSearchHistory = vi.fn();
vi.mock("@/lib/api", () => ({
  api: {
    search: (p: string, q: string) => search(p, q),
    getSearchHistory: (p: string) => getSearchHistory(p),
  },
}));

import { SearchPanel } from "@/components/SearchPanel";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

describe("SearchPanel", () => {
  beforeEach(() => {
    search.mockReset();
    getSearchHistory.mockReset();
    getSearchHistory.mockResolvedValue([]);
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
});
