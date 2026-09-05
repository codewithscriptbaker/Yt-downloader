"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import {
  ApiError,
  clearRemoteHistory,
  fetchMe,
  fetchRemoteHistory,
  login as apiLogin,
  postRemoteHistory,
  signup as apiSignup,
  type RemoteHistoryItem,
} from "@/lib/api";
import {
  clearSession,
  loadToken,
  loadUser,
  saveSession,
  type AuthUser,
} from "@/lib/authStorage";
import {
  clearHistory as clearLocalHistory,
  loadHistory,
  pushHistory,
  type HistoryItem,
} from "@/lib/storage";

type AuthContextValue = {
  user: AuthUser | null;
  ready: boolean;
  login: (email: string, password: string) => Promise<void>;
  signup: (email: string, password: string, name?: string) => Promise<void>;
  logout: () => void;
  history: HistoryItem[];
  refreshHistory: () => Promise<void>;
  recordHistory: (item: Omit<HistoryItem, "id" | "completed_at">) => Promise<void>;
  wipeHistory: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

function toLocal(item: RemoteHistoryItem): HistoryItem {
  return {
    id: item.id,
    source_url: item.source_url,
    title: item.title,
    thumbnail: item.thumbnail,
    quality: item.quality,
    audio_format: item.audio_format,
    file_name: item.file_name,
    file_size_mb: item.file_size_mb,
    completed_at: item.completed_at * (item.completed_at < 1e12 ? 1000 : 1),
  };
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [ready, setReady] = useState(false);
  const [history, setHistory] = useState<HistoryItem[]>([]);

  const refreshHistory = useCallback(async () => {
    const token = loadToken();
    if (!token) {
      setHistory(loadHistory());
      return;
    }
    try {
      const items = await fetchRemoteHistory();
      setHistory(items.map(toLocal));
    } catch {
      setHistory(loadHistory());
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const token = loadToken();
      const cached = loadUser();
      if (!token) {
        if (!cancelled) {
          setUser(null);
          setHistory(loadHistory());
          setReady(true);
        }
        return;
      }
      try {
        const me = await fetchMe();
        if (cancelled) return;
        const next: AuthUser = {
          user_id: me.user_id,
          email: me.email,
          name: me.name || "",
        };
        saveSession(token, next);
        setUser(next);
        // Merge any local guest history up to the account once
        const local = loadHistory();
        for (const item of local.slice(0, 20)) {
          try {
            await postRemoteHistory({
              source_url: item.source_url,
              title: item.title,
              thumbnail: item.thumbnail,
              quality: item.quality,
              audio_format: item.audio_format,
              file_name: item.file_name,
              file_size_mb: item.file_size_mb,
            });
          } catch {
            /* ignore merge errors */
          }
        }
        if (local.length) clearLocalHistory();
        const items = await fetchRemoteHistory();
        if (!cancelled) setHistory(items.map(toLocal));
      } catch {
        clearSession();
        if (!cancelled) {
          setUser(cached && token ? null : null);
          setHistory(loadHistory());
        }
      } finally {
        if (!cancelled) setReady(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const login = useCallback(
    async (email: string, password: string) => {
      const res = await apiLogin({ email, password });
      const next: AuthUser = {
        user_id: res.user.user_id,
        email: res.user.email,
        name: res.user.name || "",
      };
      saveSession(res.access_token, next);
      setUser(next);
      await refreshHistory();
    },
    [refreshHistory],
  );

  const signup = useCallback(
    async (email: string, password: string, name?: string) => {
      const res = await apiSignup({ email, password, name });
      const next: AuthUser = {
        user_id: res.user.user_id,
        email: res.user.email,
        name: res.user.name || "",
      };
      saveSession(res.access_token, next);
      setUser(next);
      // Push local guest history to new account
      const local = loadHistory();
      for (const item of local.slice(0, 20)) {
        try {
          await postRemoteHistory({
            source_url: item.source_url,
            title: item.title,
            thumbnail: item.thumbnail,
            quality: item.quality,
            audio_format: item.audio_format,
            file_name: item.file_name,
            file_size_mb: item.file_size_mb,
          });
        } catch {
          /* ignore */
        }
      }
      if (local.length) clearLocalHistory();
      await refreshHistory();
    },
    [refreshHistory],
  );

  const logout = useCallback(() => {
    clearSession();
    setUser(null);
    setHistory(loadHistory());
  }, []);

  const recordHistory = useCallback(
    async (item: Omit<HistoryItem, "id" | "completed_at">) => {
      if (user && loadToken()) {
        try {
          const saved = await postRemoteHistory({
            source_url: item.source_url,
            title: item.title,
            thumbnail: item.thumbnail,
            quality: item.quality,
            audio_format: item.audio_format,
            file_name: item.file_name,
            file_size_mb: item.file_size_mb,
          });
          setHistory((prev) => [
            toLocal(saved),
            ...prev.filter(
              (h) =>
                !(
                  h.source_url === saved.source_url &&
                  h.quality === saved.quality
                ),
            ),
          ]);
          return;
        } catch (err) {
          if (!(err instanceof ApiError && err.status === 401)) {
            /* fall through to local */
          }
        }
      }
      pushHistory(item);
      setHistory(loadHistory());
    },
    [user],
  );

  const wipeHistory = useCallback(async () => {
    if (user && loadToken()) {
      try {
        await clearRemoteHistory();
      } catch {
        /* ignore */
      }
      setHistory([]);
      return;
    }
    clearLocalHistory();
    setHistory([]);
  }, [user]);

  const value = useMemo(
    () => ({
      user,
      ready,
      login,
      signup,
      logout,
      history,
      refreshHistory,
      recordHistory,
      wipeHistory,
    }),
    [
      user,
      ready,
      login,
      signup,
      logout,
      history,
      refreshHistory,
      recordHistory,
      wipeHistory,
    ],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth must be used within AuthProvider");
  }
  return ctx;
}
