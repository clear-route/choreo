import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, Navigate, RouterProvider } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { NuqsAdapter } from "nuqs/adapters/react-router/v7";
import { App } from "./App";
import { RunSummary } from "./routes/RunSummary";
import { RunDetail } from "./routes/RunDetail";
import { TopicList } from "./routes/TopicList";
import { TopicDrilldown } from "./routes/TopicDrilldown";
import { AnomalyFeed } from "./routes/AnomalyFeed";
import { TopicCompare } from "./routes/TopicCompare";
import { NotFound } from "./routes/NotFound";
import { ErrorBoundary } from "./components/ui/ErrorBoundary";
import { RouteError } from "./components/ui/RouteError";
import "./index.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      gcTime: 5 * 60_000,
      refetchOnWindowFocus: true,
      retry: 1,
    },
  },
});

const router = createBrowserRouter([
  {
    path: "/",
    element: <App />,
    errorElement: <RouteError />,
    children: [
      { index: true, element: <Navigate to="/runs" replace /> },
      { path: "runs", element: <RunSummary /> },
      { path: "runs/:runId", element: <RunDetail /> },
      { path: "topics", element: <TopicList /> },
      { path: "topics/:topic", element: <TopicDrilldown /> },
      { path: "compare", element: <TopicCompare /> },
      { path: "anomalies", element: <AnomalyFeed /> },
      { path: "*", element: <NotFound /> },
    ],
  },
]);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <NuqsAdapter>
          <RouterProvider router={router} />
        </NuqsAdapter>
      </QueryClientProvider>
    </ErrorBoundary>
  </StrictMode>,
);
