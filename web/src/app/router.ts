// Hash routing: #/models, #/requests, #/playground, #/settings.
import { useSyncExternalStore } from "react";

export const PAGES = ["models", "requests", "playground", "settings"] as const;
export type Page = (typeof PAGES)[number];

function current(): Page {
  const name = window.location.hash.replace(/^#\/?/, "").split("/")[0];
  return (PAGES as readonly string[]).includes(name) ? (name as Page) : "models";
}

function subscribe(onChange: () => void) {
  window.addEventListener("hashchange", onChange);
  return () => window.removeEventListener("hashchange", onChange);
}

export function usePage(): Page {
  return useSyncExternalStore(subscribe, current);
}

export function navigate(page: Page) {
  window.location.hash = `/${page}`;
}
