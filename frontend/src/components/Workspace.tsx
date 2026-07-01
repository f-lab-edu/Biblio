"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth/AuthContext";
import { VideoSourcePanel } from "@/components/VideoSourcePanel";
import { SearchPanel } from "@/components/SearchPanel";
import { FloatingPlayer } from "@/components/FloatingPlayer";

interface PlayRequest {
  videoId: string;
  startMs: number;
  title: string;
}

export function Workspace({ projectId }: { projectId: string }) {
  const router = useRouter();
  const { userId, ready } = useAuth();
  const [playing, setPlaying] = useState<PlayRequest | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (ready && userId === null) {
      router.replace("/login");
    }
  }, [ready, userId, router]);

  if (!ready || userId === null) return null;

  async function onDeleteProject() {
    try {
      await api.deleteProject(projectId);
      router.replace("/");
    } catch {
      setError("프로젝트 삭제 요청에 실패했습니다.");
    }
  }

  return (
    <>
      <div className="flex h-screen flex-col">
        <header className="flex h-12 items-center justify-between border-b px-4">
          <span className="text-sm font-medium">워크스페이스</span>
          <button
            type="button"
            onClick={() => setConfirmDelete(true)}
            className="rounded border px-3 py-1 text-sm"
          >
            프로젝트 삭제
          </button>
        </header>
        {error && <p className="border-b px-4 py-2 text-sm text-red-600">{error}</p>}
        <div className="grid min-h-0 flex-1 grid-cols-[320px_1fr]">
          <VideoSourcePanel key={projectId} projectId={projectId} />
          <SearchPanel
            projectId={projectId}
            onPlay={(chunk) =>
              setPlaying({ videoId: chunk.videoId, startMs: chunk.startMs, title: chunk.title })
            }
          />
        </div>
      </div>
      {confirmDelete && (
        <div className="fixed inset-0 flex items-center justify-center bg-black/30 p-4">
          <div className="w-full max-w-sm rounded border bg-white p-4 shadow">
            <h2 className="text-base font-semibold">프로젝트 삭제</h2>
            <p className="mt-2 text-sm text-gray-700">영상과 검색 기록이 모두 삭제됩니다.</p>
            <div className="mt-4 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setConfirmDelete(false)}
                className="rounded border px-3 py-1 text-sm"
              >
                취소
              </button>
              <button
                type="button"
                onClick={onDeleteProject}
                className="rounded bg-black px-3 py-1 text-sm text-white"
              >
                삭제
              </button>
            </div>
          </div>
        </div>
      )}
      {playing && (
        <FloatingPlayer
          videoId={playing.videoId}
          startMs={playing.startMs}
          title={playing.title}
          onClose={() => setPlaying(null)}
        />
      )}
    </>
  );
}
