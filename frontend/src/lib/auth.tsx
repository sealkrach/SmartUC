import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { api, getToken, logout as apiLogout, setToken, type User } from "./api";

interface AuthCtx { user: User | null; loading: boolean; refresh: () => Promise<void>; logout: () => void }
const Ctx = createContext<AuthCtx>({ user: null, loading: true, refresh: async () => {}, logout: () => {} });

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = async () => {
    if (!getToken()) { setUser(null); setLoading(false); return; }
    try { setUser(await api.get<User>("/auth/me")); } catch { setUser(null); }
    setLoading(false);
  };
  useEffect(() => { void refresh(); }, []);

  const logout = () => { void apiLogout().finally(() => { setToken(null); setUser(null); location.href = "/login"; }); };
  return <Ctx.Provider value={{ user, loading, refresh, logout }}>{children}</Ctx.Provider>;
}

export const useAuth = () => useContext(Ctx);
export const isReviewer = (u: User | null) => !!u && (u.role === "reviewer" || u.role === "admin");
