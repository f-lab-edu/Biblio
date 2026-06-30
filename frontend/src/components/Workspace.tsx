"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
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

  useEffect(() => {
    if (ready && userId === null) {
      router.replace("/login");
    }
  }, [ready, userId, router]);

  if (!ready || userId === null) return null;

  return (
    <>
      <div className="grid h-screen grid-cols-[320px_1fr]">
        <VideoSourcePanel projectId={projectId} />
        <SearchPanel
          projectId={projectId}
          onPlay={(chunk) =>
            setPlaying({ videoId: chunk.videoId, startMs: chunk.startMs, title: chunk.title })
          }
        />
      </div>
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
