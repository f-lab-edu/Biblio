"use client";

import { createContext, useContext, useEffect, useState } from "react";
import { api } from "@/lib/api";

interface AuthState {
  userId: string | null;
  ready: boolean;
  signIn: (userId: string) => void;
  signOut: () => Promise<void>;
}

const AuthCtx = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [userId, setUserId] = useState<string | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api.currentUser()
      .then((user) => {
        if (!cancelled) setUserId(user.userId);
      })
      .catch(() => {
        if (!cancelled) setUserId(null);
      })
      .finally(() => {
        if (!cancelled) setReady(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  function signIn(nextUserId: string) {
    setUserId(nextUserId);
  }

  async function signOut() {
    try {
      await api.logout();
    } finally {
      setUserId(null);
    }
  }

  return (
    <AuthCtx.Provider value={{ userId, ready, signIn, signOut }}>
      {children}
    </AuthCtx.Provider>
  );
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthCtx);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
