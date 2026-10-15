/** Citation markers in model text: `[C3]`, `[C3, C5]` or `[C3; C5]`, as the backend accepts. */
const MARKER_GROUP = /\[\s*(C\d+(?:\s*[,;]\s*C\d+)*)\s*\]/gi;

export type TextPart = { kind: "text"; text: string } | { kind: "markers"; markers: string[] };

export function splitMarkers(text: string): TextPart[] {
  const parts: TextPart[] = [];
  let last = 0;
  for (const match of text.matchAll(MARKER_GROUP)) {
    const index = match.index ?? 0;
    if (index > last) parts.push({ kind: "text", text: text.slice(last, index) });
    const markers = (match[1] ?? "").split(/\s*[,;]\s*/).map(normalizeMarker);
    parts.push({ kind: "markers", markers });
    last = index + match[0].length;
  }
  if (last < text.length) parts.push({ kind: "text", text: text.slice(last) });
  return parts;
}

export function normalizeMarker(marker: string): string {
  return marker.trim().toUpperCase();
}

/** For ordering: C2 before C10. */
export function markerNumber(marker: string): number {
  return Number(marker.slice(1)) || 0;
}
