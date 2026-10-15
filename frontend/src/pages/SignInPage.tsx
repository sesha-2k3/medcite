import { useId, useState, type FormEvent } from "react";
import { Navigate, useLocation } from "react-router";

import { useAuth } from "@/auth/useAuth";
import { Button } from "@/components/ui/button";

export function SignInPage() {
  const { provider, token, reason, signIn } = useAuth();
  const location = useLocation();
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const fieldId = useId();
  const from = (location.state as { from?: string } | null)?.from ?? "/";

  if (token) return <Navigate to={from} replace />;

  if (provider !== "dev") {
    return (
      <div className="mx-auto max-w-md py-12">
        <h1 className="font-serif text-2xl font-semibold">Sign in</h1>
        <p className="text-muted-foreground mt-3">
          Sign-in with Supabase arrives with deployment. Set VITE_AUTH_PROVIDER=dev to use a
          development token.
        </p>
      </div>
    );
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    try {
      signIn(value);
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : String(problem));
    }
  }

  return (
    <div className="mx-auto max-w-md py-12">
      <h1 className="font-serif text-2xl font-semibold">Sign in with a development token</h1>
      {reason ? (
        <p role="status" className="border-ev-observational/50 mt-4 rounded-md border p-3 text-sm">
          {reason}
        </p>
      ) : null}
      <p className="text-muted-foreground mt-4 text-sm">
        Run <code className="bg-muted rounded px-1">make dev-token</code> in the project folder and
        paste its output here. A token lasts 12 hours and stays in this browser until you sign out.
      </p>
      <form onSubmit={submit} className="mt-6 space-y-3">
        <label htmlFor={fieldId} className="text-sm font-semibold">
          Token
        </label>
        <textarea
          id={fieldId}
          rows={4}
          value={value}
          spellCheck={false}
          aria-invalid={error !== null}
          onChange={(event) => {
            setValue(event.target.value);
            setError(null);
          }}
          className="bg-background block w-full rounded-md border px-3 py-2 font-mono text-xs break-all"
        />
        {error ? (
          <p role="alert" className="text-destructive text-sm">
            {error}
          </p>
        ) : null}
        <Button type="submit" disabled={value.trim().length === 0}>
          Sign in
        </Button>
      </form>
    </div>
  );
}
