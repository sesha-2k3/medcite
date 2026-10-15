import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createBrowserRouter, Navigate, RouterProvider } from "react-router";

import { ApiError } from "@/api/client";
import { RequireAuth } from "@/auth/RequireAuth";
import { AppLayout } from "@/components/AppLayout";
import { AskPage } from "@/pages/AskPage";
import { ResultPage } from "@/pages/ResultPage";
import { SignInPage } from "@/pages/SignInPage";
import { StatusPage } from "@/pages/StatusPage";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      // Client errors (401, 404) won't change on retry.
      retry: (count, error) => !(error instanceof ApiError && error.status < 500) && count < 1,
    },
  },
});

const router = createBrowserRouter([
  {
    element: <AppLayout />,
    children: [
      {
        path: "/",
        element: (
          <RequireAuth>
            <AskPage />
          </RequireAuth>
        ),
      },
      {
        path: "/r/:runId",
        element: (
          <RequireAuth>
            <ResultPage />
          </RequireAuth>
        ),
      },
      { path: "/sign-in", element: <SignInPage /> },
      { path: "/status", element: <StatusPage /> },
      { path: "*", element: <Navigate to="/" replace /> },
    ],
  },
]);

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  );
}
