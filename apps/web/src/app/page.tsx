"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useApp } from "@/lib/app-state";
import { Spinner } from "@cloudpartnerops/ui";

export default function RootPage() {
  const { me, loading } = useApp();
  const router = useRouter();
  useEffect(() => {
    if (!loading) router.replace(me ? "/overview" : "/login");
  }, [loading, me, router]);
  return (
    <div className="flex min-h-screen items-center justify-center">
      <Spinner label="Loading workspace" />
    </div>
  );
}
