"use client";

import { HistoryDrawer } from "@/components/HistoryDrawer";
import { useAuth } from "@/components/AuthProvider";
import type { HistoryItem } from "@/lib/storage";

type Props = {
  open: boolean;
  onClose: () => void;
  onReuse: (item: HistoryItem) => void;
};

export function HistoryGate({ open, onClose, onReuse }: Props) {
  const { history, wipeHistory, user, ready } = useAuth();
  if (!ready) return null;
  return (
    <HistoryDrawer
      open={open}
      onClose={onClose}
      items={history}
      cloud={Boolean(user)}
      onReuse={onReuse}
      onClear={() => void wipeHistory()}
    />
  );
}

export function useHistoryCount(): number {
  const { history, ready } = useAuth();
  if (!ready) return 0;
  return history.length;
}
