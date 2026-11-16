/**
 * Three-state colour scheme: "system" (default), "light", "dark".
 * The override lives in <meta name="color-scheme"> and localStorage; CSS maps the
 * meta to `color-scheme`, so light-dark() tokens follow it without JS branching.
 */
export type ThemeChoice = "system" | "light" | "dark";

const KEY = "color-scheme";

function meta(): HTMLMetaElement | null {
  return document.querySelector('meta[name="color-scheme"]');
}

export function getTheme(): ThemeChoice {
  const value = meta()?.content;
  return value === "light" || value === "dark" ? value : "system";
}

export function setTheme(choice: ThemeChoice): void {
  const m = meta();
  if (m) m.content = choice === "system" ? "light dark" : choice;
  try {
    if (choice === "system") localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, choice);
  } catch {
    // Storage blocked: the theme still applies for this page view.
  }
}
