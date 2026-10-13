/** Follow the operating system's light/dark preference. A manual toggle arrives in Settings. */
export function followSystemTheme(): () => void {
  const query = window.matchMedia("(prefers-color-scheme: dark)");
  const apply = (): void => {
    document.documentElement.classList.toggle("dark", query.matches);
  };
  apply();
  query.addEventListener("change", apply);
  return () => query.removeEventListener("change", apply);
}
