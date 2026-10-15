import { Link, Outlet } from "react-router";

import { useAuth } from "@/auth/useAuth";
import { ThemeToggle } from "@/components/ThemeToggle";
import { Button } from "@/components/ui/button";

export function AppLayout() {
  const { token, signOut } = useAuth();
  return (
    <div className="flex min-h-dvh flex-col">
      <a
        href="#main"
        className="bg-primary text-primary-foreground sr-only rounded px-3 py-2 focus:not-sr-only focus:absolute focus:top-2 focus:left-2"
      >
        Skip to content
      </a>
      <header className="border-b">
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-4 py-3 sm:px-6">
          <Link to="/" className="font-serif text-xl font-semibold tracking-tight">
            MedCite
          </Link>
          <nav aria-label="Main" className="flex items-center gap-2">
            {token ? (
              <Button asChild variant="ghost" size="sm">
                <Link to="/">New search</Link>
              </Button>
            ) : null}
            <ThemeToggle />
            {token ? (
              <Button variant="ghost" size="sm" onClick={() => signOut(null)}>
                Sign out
              </Button>
            ) : null}
          </nav>
        </div>
      </header>
      <main id="main" className="mx-auto w-full max-w-6xl flex-1 px-4 py-8 sm:px-6">
        <Outlet />
      </main>
      <footer className="text-muted-foreground border-t px-4 py-6 text-xs sm:px-6">
        <div className="mx-auto flex max-w-6xl flex-wrap justify-between gap-2">
          <p>
            Data from PubMed, courtesy of the U.S. National Library of Medicine. MedCite summarizes
            published research and is not medical advice.
          </p>
          <Link to="/status" className="underline-offset-2 hover:underline">
            Service status
          </Link>
        </div>
      </footer>
    </div>
  );
}
