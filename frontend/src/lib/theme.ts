/** Light, dark, or following the operating system, remembered in this browser. */

export type ThemePreference = "system" | "light" | "dark";

const STORAGE_KEY = "medcite.theme";

export function readThemePreference(): ThemePreference {
  try {
    const value = localStorage.getItem(STORAGE_KEY);
    return value === "light" || value === "dark" ? value : "system";
  } catch {
    return "system";
  }
}

export function saveThemePreference(preference: ThemePreference): void {
  try {
    localStorage.setItem(STORAGE_KEY, preference);
  } catch {
    // Storage can be unavailable (private browsing); the choice then lasts for this page only.
  }
}

/** Apply a preference; the returned function stops following the system setting. */
export function applyTheme(preference: ThemePreference): () => void {
  const root = document.documentElement;
  if (preference !== "system") {
    root.classList.toggle("dark", preference === "dark");
    return () => undefined;
  }
  const query = window.matchMedia("(prefers-color-scheme: dark)");
  const apply = (): void => {
    root.classList.toggle("dark", query.matches);
  };
  apply();
  query.addEventListener("change", apply);
  return () => query.removeEventListener("change", apply);
}
