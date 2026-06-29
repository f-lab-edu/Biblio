"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { UploadVideoInput, Video, VideoStatus } from "@/lib/api/types";

const STATUS_LABEL: Record<VideoStatus, string> = {
  PENDING: "대기",
  UPLOADED: "업로드됨",
  PROCESSING: "처리중",
  READY: "완료",
  FAILED: "실패",
  DELETING: "삭제 중",
};

export function VideoSourcePanel({ projectId }: { projectId: string }) {
  const [videos, setVideos] = useState<Video[]>([]);
  const [tab, setTab] = useState<"file" | "url">("file");
  const [title, setTitle] = useState("");
  const [sourceUrl, setSourceUrl] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    api
      .listVideos(projectId)
      .then(setVideos)
      .catch(() => setError("영상을 불러오지 못했습니다."));
  }, [projectId]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function onUpload(e: React.FormEvent) {
    e.preventDefault();
    const name = title.trim();
    if (!name) return;
    let input: UploadVideoInput;
    if (tab === "url") {
      const url = sourceUrl.trim();
      if (!url) return;
      input = { kind: "url", sourceUrl: url, title: name };
    } else {
      if (!file) return;
      input = { kind: "file", file, title: name };
    }
    try {
      const video = await api.uploadVideo(projectId, input);
      setVideos((prev) => [video, ...prev]);
      setTitle("");
      setSourceUrl("");
      setFile(null);
    } catch {
      setError("업로드에 실패했습니다.");
    }
  }

  return (
    <section className="flex h-full flex-col gap-4 border-r p-4">
      <div className="flex items-center justify-between">
        <h2 className="font-semibold">영상</h2>
        <button type="button" onClick={refresh} className="text-xs text-gray-500 underline">
          새로고침
        </button>
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
              value={sourceUrl}
              onChange={(e) => setSourceUrl(e.target.value)}
              placeholder="https://..."
              className="rounded border p-2"
            />
          </label>
        ) : (
          <label key="file-field" className="flex flex-col gap-1 text-sm">
            영상 파일
            <input
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
        {videos.map((video) => (
          <li
            key={video.id}
            className="flex items-center justify-between rounded border p-2 text-sm"
          >
            <span className="truncate">{video.title}</span>
            <span className="ml-2 shrink-0 text-xs text-gray-500">
              {STATUS_LABEL[video.status]}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}
