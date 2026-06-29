"use client";

import { createContext, useContext, useEffect, useState } from "react";
import { getToken, setToken, clearToken } from "./token";

interface AuthState {
  token: string | null;
  ready: boolean;
  signIn: (token: string) => void;
  signOut: () => void;
}

const AuthCtx = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [token, setTokenState] = useState<string | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    setTokenState(getToken());
    setReady(true);
  }, []);

  function signIn(next: string) {
    setToken(next);
    setTokenState(next);
  }

  function signOut() {
    clearToken();
    setTokenState(null);
  }

  return (
    <AuthCtx.Provider value={{ token, ready, signIn, signOut }}>
      {children}
    </AuthCtx.Provider>
  );
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthCtx);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
