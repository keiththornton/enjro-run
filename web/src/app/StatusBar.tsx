import { useEffect, useState } from "react";
import { getBinary, getSettings } from "../api/settings";

export function StatusBar() {
  const [info, setInfo] = useState<{ key: boolean; tag: string | null } | null>(null);
  useEffect(() => {
    const load = () =>
      Promise.all([getSettings(), getBinary()])
        .then(([s, b]) => setInfo({ key: s.api_key_set, tag: b.override_path ? "custom" : b.current_tag }))
        .catch(() => {});
    load();
    const t = setInterval(load, 15000);
    window.addEventListener("focus", load);
    return () => {
      clearInterval(t);
      window.removeEventListener("focus", load);
    };
  }, []);
  return (
    <footer className="statusbar">
      <span>{window.location.host}</span>
      {info && <span>key {info.key ? "set" : "off"}</span>}
      {info && <span>llama.cpp {info.tag ?? "not installed"}</span>}
    </footer>
  );
}
