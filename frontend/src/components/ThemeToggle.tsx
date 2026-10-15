import { useEffect, useState } from "react";

import {
  applyTheme,
  readThemePreference,
  saveThemePreference,
  type ThemePreference,
} from "@/lib/theme";

export function ThemeToggle() {
  const [preference, setPreference] = useState<ThemePreference>(readThemePreference);
  useEffect(() => applyTheme(preference), [preference]);

  return (
    <label className="text-sm">
      <span className="sr-only">Color theme</span>
      <select
        value={preference}
        onChange={(event) => {
          const next = event.target.value as ThemePreference;
          saveThemePreference(next);
          setPreference(next);
        }}
        className="bg-card rounded-md border px-2 py-1 text-sm"
      >
        <option value="system">System theme</option>
        <option value="light">Light</option>
        <option value="dark">Dark</option>
      </select>
    </label>
  );
}
