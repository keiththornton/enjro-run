import * as Tooltip from "@radix-ui/react-tooltip";
import { Boxes, Code2, Compass, Gauge, MessageSquare, Settings2 } from "lucide-react";
import type { ReactNode } from "react";
import { KeyPrompt } from "../components/KeyPrompt";
import { ApiPage } from "../pages/Api";
import { DiscoverPage } from "../pages/Discover";
import { ModelsPage } from "../pages/Models";
import { PlaygroundPage } from "../pages/Playground";
import { RequestsPage } from "../pages/Requests";
import { SettingsPage } from "../pages/Settings";
import { type Page, navigate, usePage } from "./router";
import { StatusBar } from "./StatusBar";

const NAV: { page: Page; label: string; icon: ReactNode }[] = [
  { page: "models", label: "Models", icon: <Boxes /> },
  { page: "discover", label: "Discover", icon: <Compass /> },
  { page: "requests", label: "Requests", icon: <Gauge /> },
  { page: "playground", label: "Playground", icon: <MessageSquare /> },
  { page: "api", label: "API", icon: <Code2 /> },
  { page: "settings", label: "Settings", icon: <Settings2 /> },
];

export function App() {
  const page = usePage();
  return (
    <Tooltip.Provider delayDuration={400}>
      <div className="shell">
        <header className="topbar">
          <a className="brand" href="#/models" aria-label="StrixRun home">
            <img src="/strix-owl.png" alt="" width={20} height={20} />
            <span>StrixRun</span>
          </a>
          <nav className="segmented" aria-label="Pages">
            {NAV.map((n) => (
              <button key={n.page} type="button" aria-label={n.label} aria-pressed={page === n.page} onClick={() => navigate(n.page)}>
                {n.icon}
                <span className="nav-label" aria-hidden>{n.label}</span>
              </button>
            ))}
          </nav>
        </header>
        <main className="page" id="main">
          {page === "models" && <ModelsPage />}
          {page === "discover" && <DiscoverPage />}
          {page === "requests" && <RequestsPage />}
          {page === "playground" && <PlaygroundPage />}
          {page === "api" && <ApiPage />}
          {page === "settings" && <SettingsPage />}
        </main>
        <StatusBar />
      </div>
      <KeyPrompt />
    </Tooltip.Provider>
  );
}
