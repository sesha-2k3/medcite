/** Which citation is selected, shared by chips (anywhere in a result) and the source panel. */
import { createContext, useContext } from "react";

import type { Citation } from "@/api/events";

export interface CitationSelection {
  citations: Record<string, Citation>;
  selected: string | null;
  /** Changes on every selection, so selecting the same chip again refocuses its source. */
  selectionKey: number;
  select: (marker: string) => void;
}

export const CitationContext = createContext<CitationSelection>({
  citations: {},
  selected: null,
  selectionKey: 0,
  select: () => undefined,
});

export function useCitations(): CitationSelection {
  return useContext(CitationContext);
}
