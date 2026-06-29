"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";

export function FloatingPlayer({
  videoId,
  startMs,
  title,
  onClose,
}: {
  videoId: string;
  startMs: number;
  title: string;
  onClose: () => void;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [src, setSrc] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    api
      .getPlaybackUrl(videoId)
      .then((url) => {
        if (active) setSrc(url);
      })
      .catch(() => {
        if (active) setError("재생 주소를 가져오지 못했습니다.");
      });
    return () => {
      active = false;
    };
  }, [videoId]);

  function onLoadedMetadata() {
    const el = videoRef.current;
    if (!el) return;
    el.currentTime = startMs / 1000;
    void el.play().catch(() => {});
  }

  return (
    <div className="fixed bottom-4 right-4 z-50 w-80 overflow-hidden rounded-lg border bg-white shadow-lg">
      <div className="flex items-center justify-between border-b p-2 text-sm">
        <span className="truncate">{title}</span>
        <button type="button" onClick={onClose} aria-label="닫기" className="ml-2 shrink-0">
          ✕
        </button>
      </div>
      {error ? (
        <p className="p-3 text-sm text-red-600">{error}</p>
      ) : (
        <video
          ref={videoRef}
          src={src ?? undefined}
          onLoadedMetadata={onLoadedMetadata}
          controls
          className="w-full"
        />
      )}
    </div>
  );
}
