"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth/AuthContext";

export function AppHeader({
  title,
  backHref,
  backLabel,
  children,
}: {
  title?: string;
  backHref?: string;
  backLabel?: string;
  children?: React.ReactNode;
}) {
  const router = useRouter();
  const { signOut } = useAuth();

  async function onSignOut() {
    await signOut();
    router.replace("/login");
  }

  return (
    <header className="flex h-12 shrink-0 items-center justify-between border-b px-4">
      <div className="flex items-center gap-3">
        {backHref && (
          <Link href={backHref} className="text-sm text-gray-600 hover:underline">
            ← {backLabel}
          </Link>
        )}
        {title && <span className="text-sm font-medium">{title}</span>}
      </div>
      <div className="flex items-center gap-2">
        {children}
        <button type="button" onClick={onSignOut} className="rounded border px-3 py-1 text-sm">
          로그아웃
        </button>
      </div>
    </header>
  );
}
