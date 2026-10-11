import * as Dialog from "@radix-ui/react-dialog";
import { KeyRound } from "lucide-react";
import { type FormEvent, useState } from "react";
import { signIn, useNeedKey } from "../api/auth";

/** Shown whenever the API answers 401: the key is exchanged for an HttpOnly session cookie. */
export function KeyPrompt() {
  const open = useNeedKey();
  const [key, setKey] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(await signIn(key.trim()));
    setBusy(false);
    setKey("");
  }

  return (
    <Dialog.Root open={open}>
      <Dialog.Portal>
        <Dialog.Overlay className="dialog-overlay" />
        <Dialog.Content className="dialog key-dialog" aria-describedby="key-desc" onEscapeKeyDown={(e) => e.preventDefault()}>
          <Dialog.Title className="dialog-title">Enter the API key</Dialog.Title>
          <Dialog.Description id="key-desc" className="dialog-description">
            This Enjro Run is shared on your network. Paste its API key to use the dashboard from this device.
          </Dialog.Description>
          <form onSubmit={submit} className="key-form">
            <label className="sr-only" htmlFor="api-key">API key</label>
            <div className="key-input">
              <KeyRound aria-hidden />
              <input
                id="api-key"
                className="text-field text-field-mono"
                type="password"
                autoComplete="current-password"
                placeholder="sk-enjro-…"
                value={key}
                onChange={(e) => setKey(e.target.value)}
                autoFocus
              />
            </div>
            {error && <p className="form-error" role="alert">{error}</p>}
            <div className="dialog-actions">
              <button type="submit" className="btn btn-md btn-primary pressable" disabled={!key.trim() || busy}>
                {busy ? "Signing in…" : "Sign in"}
              </button>
            </div>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
