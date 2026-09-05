import { Suspense } from "react";
import { DownloaderApp } from "@/components/DownloaderApp";

export default function HomePage() {
  return (
    <Suspense
      fallback={
        <div className="app-shell">
          <p className="form-hint form-hint--info">Loading…</p>
        </div>
      }
    >
      <DownloaderApp />
    </Suspense>
  );
}
