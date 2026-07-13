"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import type { Project } from "@/lib/api/types";

export function ProjectList() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [creating, setCreating] = useState(false);
  const [title, setTitle] = useState("");
  const [deletingProject, setDeletingProject] = useState<Project | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState("");
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

  function startEdit(project: Project) {
    setEditingId(project.id);
    setEditTitle(project.title);
  }

  function cancelEdit() {
    setEditingId(null);
    setEditTitle("");
  }

  async function onRename(e: React.FormEvent, projectId: string) {
    e.preventDefault();
    const name = editTitle.trim();
    if (!name) return;
    try {
      const updated = await api.renameProject(projectId, name);
      setProjects((prev) => prev.map((project) => (project.id === projectId ? updated : project)));
      cancelEdit();
    } catch {
      setError("프로젝트 이름 수정에 실패했습니다.");
    }
  }

  async function onConfirmDelete() {
    if (!deletingProject) return;
    const projectId = deletingProject.id;
    try {
      await api.deleteProject(projectId);
      setProjects((prev) => prev.filter((project) => project.id !== projectId));
      setDeletingProject(null);
    } catch {
      setError("프로젝트 삭제 요청에 실패했습니다.");
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
          <div
            key={project.id}
            className="flex min-h-28 flex-col justify-between rounded border p-4 hover:bg-gray-50"
          >
            {editingId === project.id ? (
              <form onSubmit={(e) => onRename(e, project.id)} className="flex flex-col gap-2">
                <input
                  aria-label="프로젝트 이름"
                  value={editTitle}
                  onChange={(e) => setEditTitle(e.target.value)}
                  autoFocus
                  className="rounded border p-2"
                />
                <div className="flex gap-2">
                  <button type="submit" className="rounded bg-black px-3 py-1 text-sm text-white">
                    저장
                  </button>
                  <button
                    type="button"
                    onClick={cancelEdit}
                    className="rounded border px-3 py-1 text-sm"
                  >
                    취소
                  </button>
                </div>
              </form>
            ) : (
              <Link href={`/projects/${project.id}`} className="min-w-0 font-medium">
                <span className="block truncate">{project.title}</span>
              </Link>
            )}
            <div className="mt-2 flex items-center justify-between gap-2">
              <span className="text-xs text-gray-500">영상 {project.videoCount}개</span>
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={() => startEdit(project)}
                  className="rounded border px-2 py-1 text-xs"
                >
                  이름 수정
                </button>
                <button
                  type="button"
                  onClick={() => setDeletingProject(project)}
                  className="rounded border px-2 py-1 text-xs"
                >
                  삭제
                </button>
              </div>
            </div>
          </div>
        ))}
      </div>

      {deletingProject && (
        <div className="fixed inset-0 flex items-center justify-center bg-black/30 p-4">
          <div className="w-full max-w-sm rounded border bg-white p-4 shadow">
            <h2 className="text-base font-semibold">프로젝트 삭제</h2>
            <p className="mt-2 text-sm text-gray-700">영상과 검색 기록이 모두 삭제됩니다.</p>
            <p className="mt-1 truncate text-sm font-medium">{deletingProject.title}</p>
            <div className="mt-4 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setDeletingProject(null)}
                className="rounded border px-3 py-1 text-sm"
              >
                취소
              </button>
              <button
                type="button"
                onClick={onConfirmDelete}
                className="rounded bg-black px-3 py-1 text-sm text-white"
              >
                삭제
              </button>
            </div>
          </div>
        </div>
      )}
    </main>
  );
}
