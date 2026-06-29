"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import type { Project } from "@/lib/api/types";

export function ProjectList() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [creating, setCreating] = useState(false);
  const [title, setTitle] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .listProjects()
      .then(setProjects)
      .catch(() => setError("프로젝트를 불러오지 못했습니다."));
  }, []);

  async function onCreate(e: React.FormEvent) {
    e.preventDefault();
    const name = title.trim();
    if (!name) return;
    try {
      const project = await api.createProject({ title: name });
      setProjects((prev) => [project, ...prev]);
      setTitle("");
      setCreating(false);
    } catch {
      setError("프로젝트 생성에 실패했습니다.");
    }
  }

  return (
    <main className="p-6">
      <h1 className="mb-4 text-2xl font-bold">내 프로젝트</h1>
      {error && <p className="mb-3 text-sm text-red-600">{error}</p>}
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 md:grid-cols-4">
        <div className="flex min-h-28 items-center justify-center rounded border border-dashed p-4">
          {creating ? (
            <form onSubmit={onCreate} className="flex w-full flex-col gap-2">
              <input
                aria-label="프로젝트 제목"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="제목"
                autoFocus
                className="rounded border p-2"
              />
              <div className="flex gap-2">
                <button type="submit" className="rounded bg-black px-3 py-1 text-sm text-white">
                  만들기
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setCreating(false);
                    setTitle("");
                  }}
                  className="rounded border px-3 py-1 text-sm"
                >
                  취소
                </button>
              </div>
            </form>
          ) : (
            <button
              type="button"
              onClick={() => setCreating(true)}
              className="text-sm font-medium text-gray-600"
            >
              ＋ 새 프로젝트
            </button>
          )}
        </div>

        {projects.map((project) => (
          <Link
            key={project.id}
            href={`/projects/${project.id}`}
            className="flex min-h-28 flex-col justify-between rounded border p-4 hover:bg-gray-50"
          >
            <span className="font-medium">{project.title}</span>
            <span className="mt-2 text-xs text-gray-500">영상 {project.videoCount}개</span>
          </Link>
        ))}
      </div>
    </main>
  );
}
