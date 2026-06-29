"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import type { SearchChunk, SearchResult } from "@/lib/api/types";

function formatMs(ms: number): string {
  const total = Math.floor(ms / 1000);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

interface Turn {
  query: string;
  result: SearchResult | null;
  error?: string;
}

export function SearchPanel({
  projectId,
  onPlay,
}: {
  projectId: string;
  onPlay: (chunk: SearchChunk) => void;
}) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    const q = query.trim();
    if (!q || loading) return;
    setQuery("");
    setLoading(true);
    const index = turns.length;
    setTurns((prev) => [...prev, { query: q, result: null }]);
    try {
      const result = await api.search(projectId, q);
      setTurns((prev) => prev.map((t, i) => (i === index ? { ...t, result } : t)));
    } catch {
      setTurns((prev) =>
        prev.map((t, i) => (i === index ? { ...t, error: "검색에 실패했습니다." } : t))
      );
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="flex h-screen flex-col p-4">
      <h2 className="mb-3 font-semibold">검색</h2>

      <div className="flex flex-1 flex-col gap-4 overflow-auto">
        {turns.map((turn, i) => (
          <div key={i} className="flex flex-col gap-2">
            <div className="self-end rounded bg-gray-100 px-3 py-2 text-sm">{turn.query}</div>
            {turn.error && <p className="text-sm text-red-600">{turn.error}</p>}
            {turn.result && (
              <div className="rounded border p-3 text-sm">
                <p>{turn.result.answer}</p>
                {turn.result.chunks.length > 0 && (
                  <ul className="mt-2 flex flex-col gap-1">
                    {turn.result.chunks.map((chunk) => (
                      <li key={chunk.chunkId}>
                        <button
                          type="button"
                          onClick={() => onPlay(chunk)}
                          className="text-left text-blue-600 underline"
                        >
                          [{chunk.ref}] {chunk.title} · {formatMs(chunk.startMs)}
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </div>
        ))}
      </div>

      <form onSubmit={onSubmit} className="mt-3 flex gap-2">
        <input
          aria-label="검색어"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="질문을 입력하세요"
          className="flex-1 rounded border p-2"
        />
        <button type="submit" disabled={loading} className="rounded bg-black px-4 text-sm text-white">
          검색
        </button>
      </form>
    </section>
  );
}
