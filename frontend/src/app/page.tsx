"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth/AuthContext";
import { ProjectList } from "@/components/ProjectList";

export default function Home() {
  const router = useRouter();
  const { userId, ready } = useAuth();

  useEffect(() => {
    if (ready && userId === null) {
      router.replace("/login");
    }
  }, [ready, userId, router]);

  if (!ready || userId === null) return null;

  return <ProjectList />;
}
