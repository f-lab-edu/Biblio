"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type {
  FeedbackRating,
  SearchChunk,
  SearchHistoryTurn,
  SearchResult,
} from "@/lib/api/types";

function formatMs(ms: number): string {
  const total = Math.floor(ms / 1000);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

interface Turn {
  id: string;
  query: string;
  result: SearchResult | null;
  error?: string;
}

interface FeedbackState {
  rating?: FeedbackRating;
  pending: boolean;
  error?: string;
}

function newTurnId(): string {
  return crypto.randomUUID();
}

function toHistoryTurns(history: SearchHistoryTurn[]): Turn[] {
  return history.map((turn) => ({
    id: turn.result.reqId,
    query: turn.query,
    result: turn.result,
  }));
}

function mergeHistoryTurns(current: Turn[], historyTurns: Turn[]): Turn[] {
  if (current.length === 0) return historyTurns;
  const existingIds = new Set(current.map((turn) => turn.id));
  return [
    ...historyTurns.filter((turn) => !existingIds.has(turn.id)),
    ...current,
  ];
}

function feedbackErrorMessage(error: unknown): string {
  if (typeof error === "object" && error !== null && "status" in error && error.status === 404) {
    return "이 답변에는 지금 피드백을 남길 수 없습니다.";
  }
  return "피드백 전송에 실패했습니다.";
}

function searchErrorMessage(error: unknown): string {
  if (typeof error === "object" && error !== null && "status" in error && error.status === 503) {
    return "검색 요청이 몰리고 있습니다. 잠시 후 다시 시도해 주세요.";
  }
  return "검색에 실패했습니다.";
}

function FeedbackButtons({
  state,
  onSelect,
}: {
  state: FeedbackState;
  onSelect: (rating: FeedbackRating) => void;
}) {
  const baseClass = "rounded border px-2 py-1 text-xs disabled:opacity-50";
  const activeClass = "border-black bg-black text-white";

  return (
    <div className="mt-3 flex flex-col gap-1">
      <div className="flex gap-2">
        {(["LIKE", "DISLIKE"] as const).map((rating) => {
          const selected = state.rating === rating;
          return (
            <button
              key={rating}
              type="button"
              aria-pressed={selected}
              disabled={state.pending}
              onClick={() => onSelect(rating)}
              className={`${baseClass} ${selected ? activeClass : "bg-white"}`}
            >
              {rating === "LIKE" ? "좋아요" : "싫어요"}
            </button>
          );
        })}
      </div>
      {state.error && <p className="text-xs text-red-600">{state.error}</p>}
    </div>
  );
}

function CitationList({
  chunks,
  onPlay,
}: {
  chunks: SearchChunk[];
  onPlay: (chunk: SearchChunk) => void;
}) {
  return (
    <ul className="mt-2 flex flex-col gap-1">
      {chunks.map((chunk) => (
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
  );
}

function SearchTurn({
  turn,
  feedbackState,
  onFeedback,
  onPlay,
}: {
  turn: Turn;
  feedbackState: FeedbackState;
  onFeedback: (reqId: string, rating: FeedbackRating) => void;
  onPlay: (chunk: SearchChunk) => void;
}) {
  const result = turn.result;

  return (
    <div className="flex flex-col gap-2">
      <div className="self-end rounded bg-gray-100 px-3 py-2 text-sm">{turn.query}</div>
      {turn.error && <p className="text-sm text-red-600">{turn.error}</p>}
      {result && (
        <div className="rounded border p-3 text-sm">
          <p>{result.answer}</p>
          {result.chunks.length > 0 && (
            <>
              <CitationList chunks={result.chunks} onPlay={onPlay} />
              <FeedbackButtons
                state={feedbackState}
                onSelect={(rating) => onFeedback(result.reqId, rating)}
              />
            </>
          )}
        </div>
      )}
    </div>
  );
}

function useSearchTurns(projectId: string) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const endRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .getSearchHistory(projectId)
      .then((history) => {
        if (!cancelled) {
          setTurns((current) => mergeHistoryTurns(current, toHistoryTurns(history)));
        }
      })
      .catch(() => {
        return;
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  useEffect(() => {
    endRef.current?.scrollIntoView?.({ block: "end" });
  }, [turns]);

  return { turns, setTurns, endRef };
}

function useFeedbackState() {
  const [feedbackByReqId, setFeedbackByReqId] = useState<Record<string, FeedbackState>>({});

  async function submitTurnFeedback(reqId: string, rating: FeedbackRating) {
    const current = feedbackByReqId[reqId] ?? { pending: false };
    if (current.pending || current.rating === rating) return;
    setFeedbackByReqId((prev) => ({
      ...prev,
      [reqId]: { ...prev[reqId], pending: true, error: undefined },
    }));
    try {
      await api.submitFeedback(reqId, rating);
      setFeedbackByReqId((prev) => ({ ...prev, [reqId]: { rating, pending: false } }));
    } catch (error) {
      setFeedbackByReqId((prev) => ({
        ...prev,
        [reqId]: {
          rating: prev[reqId]?.rating,
          pending: false,
          error: feedbackErrorMessage(error),
        },
      }));
    }
  }

  return { feedbackByReqId, submitTurnFeedback };
}

function SearchResults({
  turns,
  feedbackByReqId,
  endRef,
  onFeedback,
  onPlay,
}: {
  turns: Turn[];
  feedbackByReqId: Record<string, FeedbackState>;
  endRef: React.RefObject<HTMLDivElement | null>;
  onFeedback: (reqId: string, rating: FeedbackRating) => void;
  onPlay: (chunk: SearchChunk) => void;
}) {
  return (
    <div className="flex flex-1 flex-col gap-4 overflow-auto">
      {turns.map((turn) => (
        <SearchTurn
          key={turn.id}
          turn={turn}
          feedbackState={
            turn.result ? feedbackByReqId[turn.result.reqId] ?? { pending: false } : { pending: false }
          }
          onFeedback={onFeedback}
          onPlay={onPlay}
        />
      ))}
      <div ref={endRef} />
    </div>
  );
}

function SearchForm({
  query,
  loading,
  onQueryChange,
  onSubmit,
}: {
  query: string;
  loading: boolean;
  onQueryChange: (query: string) => void;
  onSubmit: (e: React.FormEvent) => void;
}) {
  return (
    <form onSubmit={onSubmit} className="mt-3 flex gap-2">
      <input
        aria-label="검색어"
        value={query}
        onChange={(e) => onQueryChange(e.target.value)}
        placeholder="질문을 입력하세요"
        className="flex-1 rounded border p-2"
      />
      <button type="submit" disabled={loading} className="rounded bg-black px-4 text-sm text-white">
        검색
      </button>
    </form>
  );
}

export function SearchPanel({
  projectId,
  onPlay,
}: {
  projectId: string;
  onPlay: (chunk: SearchChunk) => void;
}) {
  const { turns, setTurns, endRef } = useSearchTurns(projectId);
  const { feedbackByReqId, submitTurnFeedback } = useFeedbackState();
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    const q = query.trim();
    if (!q || loading) return;
    setQuery("");
    setLoading(true);
    const turnId = newTurnId();
    setTurns((prev) => [...prev, { id: turnId, query: q, result: null }]);
    try {
      const result = await api.search(projectId, q);
      setTurns((prev) => prev.map((t) => (t.id === turnId ? { ...t, result } : t)));
    } catch (error) {
      setTurns((prev) =>
        prev.map((t) => (t.id === turnId ? { ...t, error: searchErrorMessage(error) } : t))
      );
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="flex h-screen flex-col p-4">
      <h2 className="mb-3 font-semibold">검색</h2>
      <SearchResults
        turns={turns}
        feedbackByReqId={feedbackByReqId}
        endRef={endRef}
        onFeedback={submitTurnFeedback}
        onPlay={onPlay}
      />
      <SearchForm query={query} loading={loading} onQueryChange={setQuery} onSubmit={onSubmit} />
    </section>
  );
}
