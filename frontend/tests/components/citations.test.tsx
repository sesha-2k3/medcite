import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";

import { ResultView } from "@/components/ResultView";
import type { Run } from "@/stores/runs";
import type { StreamState } from "@/stream/reducer";

import { QUESTION_EVENTS, reduceEvents } from "../fixtures/stream";

function renderRun(state: StreamState) {
  const run: Run = {
    id: "r1",
    request: { kind: "ask", question: "Dumbbell vs barbell curls?", history: [] },
    parentId: null,
    startedAt: 0,
    finishedAt: 30_000,
    state,
  };
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ResultView run={run} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("citation chips and the source panel", () => {
  it("renders one chip per marker in a group", () => {
    renderRun(reduceEvents(QUESTION_EVENTS));
    expect(screen.getAllByRole("button", { name: /^Source C1/ }).length).toBeGreaterThan(1);
    expect(screen.getByRole("button", { name: /^Source C2/ })).toBeEnabled();
  });

  it("selecting a chip highlights and focuses its passage", async () => {
    const user = userEvent.setup();
    renderRun(reduceEvents(QUESTION_EVENTS));
    const chip = screen.getByRole("button", { name: /^Source C2/ });
    await user.click(chip);

    const heading = screen.getByRole("heading", { name: "PMID 90000002" });
    expect(heading).toHaveFocus();
    expect(heading.closest("article")).toHaveAttribute("aria-current", "true");
    expect(chip).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("Placeholder abstract passage.")).toBeVisible();
  });

  it("titles a source from the evidence table when it is there", async () => {
    const user = userEvent.setup();
    renderRun(reduceEvents(QUESTION_EVENTS));
    const [chip] = screen.getAllByRole("button", { name: /^Source C1/ });
    if (!chip) throw new Error("no C1 chip");
    await user.click(chip);
    expect(
      screen.getByRole("heading", {
        name: "Electromyographic activity of the biceps in curl variations",
      }),
    ).toHaveFocus();
  });

  it("disables a chip whose citation has not arrived", () => {
    renderRun(reduceEvents([["token", { text: "A claim [C9]." }]]));
    expect(screen.getByRole("button", { name: /^Source C9/ })).toBeDisabled();
  });
});
