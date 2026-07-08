"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { FormEvent } from "react";
import type { UploadVideoInput, Video, VideoStatus } from "@/lib/api/types";

const STATUS_LABEL: Record<VideoStatus, string> = {
  PENDING: "대기",
  UPLOADED: "업로드됨",
  PROCESSING: "처리중",
  READY: "완료",
  FAILED: "실패",
  DELETING: "삭제 중",
};

const YOUTUBE_HOSTS = new Set([
  "youtube.com",
  "www.youtube.com",
  "m.youtube.com",
  "music.youtube.com",
  "youtu.be",
]);

function isYoutubeUrl(value: string) {
  try {
    return YOUTUBE_HOSTS.has(new URL(value).hostname.toLowerCase());
  } catch {
    return false;
  }
}

type UploadState = {
  video: Video;
  progress: number;
  phase: "uploading" | "failed";
};

function upsertVideo(videos: Video[], nextVideo: Video) {
  return [nextVideo, ...videos.filter((video) => video.id !== nextVideo.id)];
}

export function VideoSourcePanel({ projectId }: { projectId: string }) {
  const [videos, setVideos] = useState<Video[]>([]);
  const [uploadStates, setUploadStates] = useState<Record<string, UploadState>>({});
  const [tab, setTab] = useState<"file" | "url">("file");
  const [title, setTitle] = useState("");
  const [sourceUrl, setSourceUrl] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [fileInputKey, setFileInputKey] = useState(0);
  const [selectedVideoIds, setSelectedVideoIds] = useState<Set<string>>(new Set());
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const refresh = useCallback(() => {
    api
      .listVideos(projectId)
      .then((nextVideos) => {
        if (mountedRef.current) setVideos(nextVideos);
      })
      .catch(() => {
        if (mountedRef.current) setError("영상을 불러오지 못했습니다.");
      });
  }, [projectId]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const displayVideos = useMemo(() => {
    const listedVideoIds = new Set(videos.map((video) => video.id));
    const localUploadVideos = Object.values(uploadStates)
      .map((state) => state.video)
      .filter((video) => !listedVideoIds.has(video.id));
    return [...localUploadVideos, ...videos];
  }, [uploadStates, videos]);

  async function onUpload(e: FormEvent) {
    e.preventDefault();
    setError(null);
    const name = title.trim();
    if (!name) return;
    let input: UploadVideoInput;
    if (tab === "url") {
      const url = sourceUrl.trim();
      if (!url) return;
      if (!isYoutubeUrl(url)) {
        setError("YouTube URL만 등록할 수 있습니다.");
        return;
      }
      input = { kind: "url", sourceUrl: url, title: name };
    } else {
      if (!file) return;
      input = { kind: "file", file, title: name };
    }
    setTitle("");
    setSourceUrl("");
    setFile(null);
    setFileInputKey((prev) => prev + 1);

    let createdVideoId: string | null = null;
    try {
      if (input.kind === "url") {
        const video = await api.uploadVideo(projectId, input);
        if (mountedRef.current) setVideos((prev) => upsertVideo(prev, video));
        return;
      }

      const video = await api.uploadVideo(projectId, input, {
        onUploadCreated: (createdVideo) => {
          createdVideoId = createdVideo.id;
          if (!mountedRef.current) return;
          setUploadStates((prev) => ({
            ...prev,
            [createdVideo.id]: { video: createdVideo, progress: 0, phase: "uploading" },
          }));
        },
        onProgress: (percent) => {
          const videoId = createdVideoId;
          if (!videoId || !mountedRef.current) return;
          setUploadStates((prev) => {
            const current = prev[videoId];
            if (!current) return prev;
            return {
              ...prev,
              [videoId]: { ...current, progress: percent, phase: "uploading" },
            };
          });
        },
      });
      if (!mountedRef.current) return;
      setUploadStates((prev) => {
        if (!createdVideoId) return prev;
        const next = { ...prev };
        delete next[createdVideoId];
        return next;
      });
      setVideos((prev) => upsertVideo(prev, video));
    } catch {
      if (!mountedRef.current) return;
      if (createdVideoId) {
        const videoId = createdVideoId;
        setUploadStates((prev) => {
          const current = prev[videoId];
          if (!current) return prev;
          return {
            ...prev,
            [videoId]: { ...current, phase: "failed" },
          };
        });
      }
      setError("업로드에 실패했습니다.");
    }
  }

  function toggleVideo(videoId: string) {
    if (uploadStates[videoId]?.phase === "uploading") return;
    setSelectedVideoIds((prev) => {
      const next = new Set(prev);
      if (next.has(videoId)) {
        next.delete(videoId);
      } else {
        next.add(videoId);
      }
      return next;
    });
  }

  async function onDeleteSelected() {
    const targets = Array.from(selectedVideoIds);
    if (targets.length === 0) return;
    setDeleting(true);
    try {
      await api.deleteVideos(targets);
      setVideos((prev) => prev.filter((video) => !selectedVideoIds.has(video.id)));
      setUploadStates((prev) => {
        const next = { ...prev };
        for (const videoId of targets) {
          delete next[videoId];
        }
        return next;
      });
      setSelectedVideoIds(new Set());
    } catch {
      setError("영상 삭제 요청에 실패했습니다.");
    } finally {
      setDeleting(false);
    }
  }

  return (
    <section className="flex h-full flex-col gap-4 border-r p-4">
      <div className="flex items-center justify-between">
        <h2 className="font-semibold">영상</h2>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={onDeleteSelected}
            disabled={selectedVideoIds.size === 0 || deleting}
            className="rounded border px-2 py-1 text-xs disabled:opacity-40"
          >
            삭제
          </button>
          <button type="button" onClick={refresh} className="text-xs text-gray-500 underline">
            새로고침
          </button>
        </div>
      </div>

      <form onSubmit={onUpload} className="flex flex-col gap-2 rounded border p-3">
        <div className="flex gap-2 text-sm">
          <button
            type="button"
            onClick={() => setTab("file")}
            className={tab === "file" ? "font-semibold underline" : "text-gray-500"}
          >
            파일
          </button>
          <button
            type="button"
            onClick={() => setTab("url")}
            className={tab === "url" ? "font-semibold underline" : "text-gray-500"}
          >
            URL
          </button>
        </div>

        <label className="flex flex-col gap-1 text-sm">
          영상 제목
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            className="rounded border p-2"
          />
        </label>

        {tab === "url" ? (
          <label key="url-field" className="flex flex-col gap-1 text-sm">
            영상 URL
            <input
              aria-label="영상 URL"
              value={sourceUrl}
              onChange={(e) => setSourceUrl(e.target.value)}
              placeholder="https://www.youtube.com/watch?v=..."
              className="rounded border p-2"
            />
            <span className="text-xs text-gray-500">YouTube URL만 지원합니다.</span>
          </label>
        ) : (
          <label key="file-field" className="flex flex-col gap-1 text-sm">
            영상 파일
            <input
              key={fileInputKey}
              type="file"
              accept="video/*"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              className="text-sm"
            />
          </label>
        )}

        <button type="submit" className="rounded bg-black px-3 py-1 text-sm text-white">
          업로드
        </button>
      </form>

      {error && <p className="text-sm text-red-600">{error}</p>}

      <ul className="flex flex-col gap-2 overflow-auto">
        {displayVideos.map((video) => {
          const uploadState = uploadStates[video.id];
          const isUploading = uploadState?.phase === "uploading";
          return (
            <li key={video.id} className="flex items-center gap-2 rounded border p-2 text-sm">
              <input
                type="checkbox"
                aria-label={`${video.title} 선택`}
                checked={!isUploading && selectedVideoIds.has(video.id)}
                disabled={isUploading}
                onChange={() => toggleVideo(video.id)}
                className="size-4 shrink-0"
              />
              <span className="min-w-0 flex-1 truncate">{video.title}</span>
              <span className="ml-2 shrink-0 text-xs text-gray-500">
                {uploadState?.phase === "uploading" ? (
                  `업로드 중 ${uploadState.progress}%`
                ) : uploadState?.phase === "failed" ? (
                  "업로드 실패"
                ) : video.status === "PROCESSING" ? (
                  <span className="inline-flex items-center gap-1">
                    <span
                      aria-label="처리 중"
                      className="size-3 animate-spin rounded-full border-2 border-gray-300 border-t-gray-700"
                    />
                    처리중
                  </span>
                ) : (
                  STATUS_LABEL[video.status]
                )}
              </span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
