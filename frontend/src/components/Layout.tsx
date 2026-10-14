import clsx from "clsx";
import { Boxes, FileInput, GitFork, LayoutDashboard, Library, LogOut, MessageSquarePlus, Network, Plug, ScrollText, UserCog, Users } from "lucide-react";
import { NavLink, Outlet } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { isReviewer, useAuth } from "../lib/auth";
import { LlmStatusButton } from "./LlmSettings";

const NAV = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard, end: true },
  { to: "/usecases", label: "Registry", icon: Boxes },
  { to: "/intake", label: "New use case", icon: MessageSquarePlus },
  { to: "/relations", label: "Relations inbox", icon: GitFork, badge: true },
  { to: "/graph", label: "Portfolio graph", icon: Network },
  { to: "/import", label: "Import", icon: FileInput, reviewer: true },
  { to: "/templates", label: "Templates", icon: Library },
  { to: "/mcp", label: "MCP & API", icon: Plug },
  { to: "/audit", label: "Audit trail", icon: ScrollText, reviewer: true },
  { to: "/account", label: "My account", icon: UserCog },
  { to: "/admin", label: "Users", icon: Users, admin: true },
];

export default function Layout() {
  const { user, logout } = useAuth();
  const dash = useQuery({ queryKey: ["dashboard"], queryFn: () => api.get<{ suggested_relations: number }>("/portfolio/dashboard"), refetchInterval: 30000 });
  return (
    <div className="flex h-full">
      <aside className="w-60 shrink-0 bg-slate-900 text-slate-200 flex flex-col">
        <div className="px-5 py-5 border-b border-slate-800">
          <div className="flex items-center gap-2">
            <div className="h-7 w-7 rounded-lg bg-indigo-500 grid place-items-center font-bold text-white">U</div>
            <div>
              <div className="font-semibold leading-tight">UseCase Hub</div>
              <div className="text-[11px] text-slate-400">AI portfolio registry</div>
            </div>
          </div>
        </div>
        <nav className="flex-1 px-3 py-3 space-y-0.5">
          {NAV.filter((n) => (!n.reviewer || isReviewer(user)) && (!n.admin || user?.role === "admin")).map((n) => (
            <NavLink key={n.to} to={n.to} end={n.end}
              className={({ isActive }) => clsx("flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition", isActive ? "bg-slate-800 text-white" : "text-slate-300 hover:bg-slate-800/60 hover:text-white")}>
              <n.icon className="h-4 w-4" />
              <span className="flex-1">{n.label}</span>
              {n.badge && !!dash.data?.suggested_relations && (
                <span className="rounded-full bg-amber-500 text-slate-900 text-[10px] font-bold px-1.5 py-0.5 tabular-nums">{dash.data.suggested_relations}</span>
              )}
            </NavLink>
          ))}
        </nav>
        <div className="px-4 py-4 border-t border-slate-800 text-sm">
          <div className="truncate font-medium">{user?.full_name || user?.email}</div>
          <div className="flex items-center justify-between mt-1">
            <span className="chip bg-slate-800 text-slate-300 capitalize">{user?.role}</span>
            <button className="text-slate-400 hover:text-white" onClick={logout} title="Sign out"><LogOut className="h-4 w-4" /></button>
          </div>
        </div>
      </aside>
      <main className="flex-1 min-w-0 overflow-auto relative">
        <div className="sticky top-0 z-30 flex justify-end items-center h-12 px-6 bg-slate-50/90 backdrop-blur border-b border-slate-200/60">
          <LlmStatusButton />
        </div>
        <div className="max-w-[1400px] mx-auto px-6 py-5"><Outlet /></div>
      </main>
    </div>
  );
}
