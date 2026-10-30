import { useEffect, useState, type FormEvent } from "react";
import { clearApiKey, getApiKey, listSessions, setApiKey, setUnauthorizedHandler } from "./api";
import { LoginPage } from "./pages/LoginPage";
import { initMiniApp, isMiniApp } from "./telegram";

const EMBEDDED_KEY = (import.meta.env.VITE_API_KEY as string | undefined)?.trim() ?? "";

export function App() {
  const [unlocked, setUnlocked] = useState(false);
  const [ready, setReady] = useState(false);
  const [bootError, setBootError] = useState("");

  useEffect(() => {
    initMiniApp();
    setUnauthorizedHandler(() => {
      if (!EMBEDDED_KEY) {
        clearApiKey();
        setUnlocked(false);
      }
    });
  }, []);

  useEffect(() => {
    const existing = getApiKey() || EMBEDDED_KEY;
    if (!existing) {
      setReady(true);
      return;
    }
    setApiKey(existing);
    listSessions()
      .then(() => setUnlocked(true))
      .catch(() => {
        if (EMBEDDED_KEY) {
          setBootError("Неверный встроенный API-ключ");
        } else {
          clearApiKey();
          setUnlocked(false);
        }
      })
      .finally(() => setReady(true));
  }, []);

  if (!ready) {
    return <p className="boot">Загрузка…</p>;
  }
  if (!unlocked) {
    if (isMiniApp() && EMBEDDED_KEY) {
      return <p className="boot">{bootError || "Загрузка…"}</p>;
    }
    return <ApiKeyGate onUnlock={() => setUnlocked(true)} />;
  }
  return <LoginPage />;
}

function ApiKeyGate({ onUnlock }: { onUnlock: () => void }) {
  const [value, setValue] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setApiKey(value);
    try {
      await listSessions();
      onUnlock();
    } catch (err) {
      clearApiKey();
      setError(err instanceof Error ? err.message : "Не удалось подключиться");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="gate">
      <form className="card gate-card stack" onSubmit={submit}>
        <label className="field">
          <span>API-ключ</span>
          <input value={value} onChange={(event) => setValue(event.target.value)} autoComplete="off" required />
        </label>
        {error ? <p className="banner bad">{error}</p> : null}
        <button className="primary" type="submit" disabled={busy}>
          {busy ? "Проверка…" : "Далее"}
        </button>
      </form>
    </div>
  );
}
