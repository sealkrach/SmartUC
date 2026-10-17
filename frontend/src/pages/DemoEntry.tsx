import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Spinner } from "../components/ui";
import { guestLogin } from "../lib/api";
import { useAuth } from "../lib/auth";

/** /demo — shareable link that opens the read-only demo without an account. */
export default function DemoEntry() {
  const nav = useNavigate();
  const { refresh } = useAuth();
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    guestLogin().then(refresh).then(() => nav("/", { replace: true })).catch((e: Error) => setError(e.message));
  }, []);
  return (
    <div className="min-h-full grid place-items-center">
      {error ? <div className="card p-6 text-center"><p className="font-medium">{error}</p><a href="/login" className="text-indigo-600 text-sm hover:underline">Sign in instead</a></div> : <Spinner />}
    </div>
  );
}
