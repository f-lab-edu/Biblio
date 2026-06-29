import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const search = vi.fn();
vi.mock("@/lib/api", () => ({ api: { search: (p: string, q: string) => search(p, q) } }));

import { SearchPanel } from "@/components/SearchPanel";

describe("SearchPanel", () => {
  beforeEach(() => search.mockReset());

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
});
