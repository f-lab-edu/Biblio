import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const getPlaybackUrl = vi.fn();
vi.mock("@/lib/api", () => ({ api: { getPlaybackUrl: (id: string) => getPlaybackUrl(id) } }));

import { FloatingPlayer } from "@/components/FloatingPlayer";

describe("FloatingPlayer", () => {
  beforeEach(() => getPlaybackUrl.mockReset());

  it("loads the playback url and shows the title", async () => {
    getPlaybackUrl.mockResolvedValue("https://play/url.mp4");
    render(<FloatingPlayer videoId="v1" startMs={30000} title="강의1" onClose={vi.fn()} />);
    expect(screen.getByText("강의1")).toBeInTheDocument();
    expect(getPlaybackUrl).toHaveBeenCalledWith("v1");
  });

  it("calls onClose when the close button is clicked", async () => {
    getPlaybackUrl.mockResolvedValue("https://play/url.mp4");
    const onClose = vi.fn();
    render(<FloatingPlayer videoId="v1" startMs={0} title="강의1" onClose={onClose} />);
    await userEvent.click(screen.getByRole("button", { name: "닫기" }));
    expect(onClose).toHaveBeenCalled();
  });
});
