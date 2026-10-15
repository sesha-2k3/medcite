import { TriangleAlert } from "lucide-react";

export function Warnings({ messages }: { messages: string[] }) {
  if (messages.length === 0) return null;
  return (
    <ul aria-label="Warnings" className="space-y-2">
      {messages.map((message) => (
        <li
          key={message}
          className="border-ev-observational/50 flex gap-2 rounded-md border p-3 text-sm"
        >
          <TriangleAlert aria-hidden className="text-ev-observational mt-0.5 size-4 shrink-0" />
          {message}
        </li>
      ))}
    </ul>
  );
}
