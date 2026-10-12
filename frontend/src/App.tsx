import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Navigate, Outlet, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import { Spinner } from "./components/ui";
import { AuthProvider, useAuth } from "./lib/auth";
import Admin from "./pages/Admin";
import Dashboard from "./pages/Dashboard";
import Graph from "./pages/Graph";
import Import from "./pages/Import";
import Intake from "./pages/Intake";
import Login from "./pages/Login";
import NewUseCase from "./pages/NewUseCase";
import Registry from "./pages/Registry";
import RelationsInbox from "./pages/RelationsInbox";
import Templates from "./pages/Templates";
import UseCaseDetail from "./pages/UseCaseDetail";

const qc = new QueryClient({ defaultOptions: { queries: { retry: 1, staleTime: 10_000, refetchOnWindowFocus: false } } });

function Protected() {
  const { user, loading } = useAuth();
  if (loading) return <div className="h-full grid place-items-center"><Spinner /></div>;
  if (!user) return <Navigate to="/login" replace />;
  return <Outlet />;
}

export default function App() {
  return (
    <QueryClientProvider client={qc}>
      <AuthProvider>
        <BrowserRouter>
          <Routes>
            <Route path="/login" element={<Login />} />
            <Route element={<Protected />}>
              <Route element={<Layout />}>
                <Route path="/" element={<Dashboard />} />
                <Route path="/usecases" element={<Registry />} />
                <Route path="/usecases/new" element={<NewUseCase />} />
                <Route path="/usecases/:id" element={<UseCaseDetail />} />
                <Route path="/intake" element={<Intake />} />
                <Route path="/intake/:id" element={<Intake />} />
                <Route path="/relations" element={<RelationsInbox />} />
                <Route path="/graph" element={<Graph />} />
                <Route path="/import" element={<Import />} />
                <Route path="/templates" element={<Templates />} />
                <Route path="/admin" element={<Admin />} />
                <Route path="*" element={<Navigate to="/" replace />} />
              </Route>
            </Route>
          </Routes>
        </BrowserRouter>
      </AuthProvider>
    </QueryClientProvider>
  );
}
