import { useEffect, useRef, useState } from "react";
import { sendCode, signIn } from "../api";
import { getWebApp, initMiniApp, isMiniApp } from "../telegram";
import type { SessionPublic } from "../types";

type Step = "phone" | "code" | "password" | "done";

const LABELS: Record<Exclude<Step, "done">, string> = {
  phone: "Далее",
  code: "ОК",
  password: "ОК",
};

function phoneDigits(value: string): string {
  return value.replace(/\D/g, "");
}

/** Российский мобильный: 10 цифр (9XXXXXXXXX) или 11 (7… / 8…). */
function isPhoneComplete(value: string): boolean {
  const digits = phoneDigits(value);
  if (digits.length === 10 && digits.startsWith("9")) return true;
  if (digits.length === 11 && (digits.startsWith("7") || digits.startsWith("8"))) return true;
  return false;
}

/** На API отправляем только цифры — «+» не добавляем и не передаём. */
function phoneForRequest(value: string): string {
  return phoneDigits(value);
}

function readIncomingPhone(): string {
  const params = new URLSearchParams(window.location.search);
  const fromQuery = params.get("phone") ?? params.get("tel") ?? "";
  if (fromQuery.trim()) return fromQuery.trim();

  const startParam = (window.Telegram?.WebApp as { initDataUnsafe?: { start_param?: string } } | undefined)
    ?.initDataUnsafe?.start_param;
  if (startParam && phoneDigits(startParam).length >= 10) {
    return startParam.trim();
  }
  return "";
}

/** Бот, з якого відкрито мініапп: ?bot=MyBot у URL WebApp кожного бота. */
function readSourceBot(): string | null {
  const params = new URLSearchParams(window.location.search);
  const raw = (params.get("bot") ?? params.get("source_bot") ?? "").trim().replace(/^@/, "");
  return raw || null;
}

const autoSentPhones = new Set<string>();

export function LoginPage() {
  const incoming = readIncomingPhone();
  const sourceBot = readSourceBot();
  const [step, setStep] = useState<Step>(() => (isPhoneComplete(incoming) ? "code" : "phone"));
  const [phone, setPhone] = useState(incoming);
  const [loginId, setLoginId] = useState("");
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<SessionPublic | null>(null);
  const mini = isMiniApp();
  const actionRef = useRef<() => void>(() => undefined);
  const phoneOk = isPhoneComplete(phone);

  useEffect(() => {
    initMiniApp();
  }, []);

  useEffect(() => {
    if (!isPhoneComplete(incoming)) return;
    const key = phoneForRequest(incoming);
    if (autoSentPhones.has(key)) return;
    autoSentPhones.add(key);
    void requestCode(incoming);
  }, [incoming]);

  useEffect(() => {
    const app = getWebApp();
    if (!app || !mini) return;

    const onClick = () => actionRef.current();
    app.MainButton.onClick(onClick);
    return () => {
      app.MainButton.offClick(onClick);
      app.MainButton.hide();
      app.MainButton.hideProgress();
    };
  }, [mini]);

  useEffect(() => {
    const app = getWebApp();
    if (!app || !mini) return;
    if (step === "done") {
      app.MainButton.hide();
      return;
    }
    // Пока автоотправка кода — на шаге code без loginId ещё ждём ответ
    if (step === "code" && !loginId && busy) {
      app.MainButton.setText("Подождите…");
      app.MainButton.show();
      app.MainButton.showProgress(false);
      app.MainButton.disable();
      return;
    }
    app.MainButton.setText(busy ? "Подождите…" : LABELS[step]);
    app.MainButton.show();
    if (busy) {
      app.MainButton.showProgress(false);
      app.MainButton.disable();
    } else {
      app.MainButton.hideProgress();
      app.MainButton.enable();
    }
  }, [step, busy, mini, loginId]);

  async function requestCode(overridePhone?: string) {
    const raw = overridePhone ?? phone;
    if (!isPhoneComplete(raw)) {
      setError("Введите номер телефона");
      setStep("phone");
      getWebApp()?.HapticFeedback?.notificationOccurred("error");
      return;
    }
    setPhone(raw);
    setBusy(true);
    setError("");
    try {
      const response = await sendCode({
        phone: phoneForRequest(raw),
        source_bot: sourceBot,
      });
      setLoginId(response.login_id);
      setCode("");
      setPassword("");
      setStep("code");
      getWebApp()?.HapticFeedback?.notificationOccurred("success");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ошибка");
      setStep("phone");
      getWebApp()?.HapticFeedback?.notificationOccurred("error");
    } finally {
      setBusy(false);
    }
  }

  async function confirmCode() {
    if (!loginId) return;
    if (!code.trim()) {
      setError("Введите код");
      getWebApp()?.HapticFeedback?.notificationOccurred("error");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const response = await signIn({
        login_id: loginId,
        code: code.replace(/\s/g, ""),
        password: undefined,
      });
      if (response.password_required) {
        setStep("password");
        return;
      }
      if (response.session) {
        finish(response.session);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ошибка");
      getWebApp()?.HapticFeedback?.notificationOccurred("error");
    } finally {
      setBusy(false);
    }
  }

  async function confirmPassword() {
    if (!password.trim()) {
      setError("Введите пароль");
      getWebApp()?.HapticFeedback?.notificationOccurred("error");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const response = await signIn({
        login_id: loginId,
        code: code.replace(/\s/g, ""),
        password: password.trim(),
      });
      if (response.password_required) {
        setError("Нужен пароль двухэтапной проверки");
        return;
      }
      if (response.session) {
        finish(response.session);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ошибка");
      getWebApp()?.HapticFeedback?.notificationOccurred("error");
    } finally {
      setBusy(false);
    }
  }

  function finish(session: SessionPublic) {
    setDone(session);
    setStep("done");
    setError("");
    getWebApp()?.HapticFeedback?.notificationOccurred("success");
    window.setTimeout(() => getWebApp()?.close(), 1200);
  }

  actionRef.current = () => {
    if (busy) return;
    if (step === "phone") void requestCode();
    else if (step === "code") void confirmCode();
    else if (step === "password") void confirmPassword();
  };

  const waitingCodeSend = step === "code" && !loginId && busy;

  return (
    <div className="miniapp">
      {error ? <p className="banner bad">{error}</p> : null}

      {step === "done" && done ? (
        <p className="banner good">
          Готово
          {done.username ? ` · @${done.username}` : done.phone ? ` · ${done.phone}` : ""}
        </p>
      ) : null}

      {step === "phone" ? (
        <label className="field">
          <span className="field-label">
            Телефон <em className="required">*обязательно</em>
          </span>
          <div className={phoneOk ? "phone-wrap ok" : "phone-wrap"}>
            <input
              value={phone}
              onChange={(event) => setPhone(event.target.value)}
              placeholder="+7XXXXXXXXXX"
              inputMode="tel"
              autoComplete="tel"
              autoFocus
            />
            {phoneOk ? (
              <span className="phone-check" aria-hidden="true">
                ✓
              </span>
            ) : null}
          </div>
        </label>
      ) : null}

      {step === "code" ? (
        waitingCodeSend ? (
          <p className="muted">Отправляем код…</p>
        ) : (
          <label className="field">
            <span>Код</span>
            <input
              value={code}
              onChange={(event) => setCode(event.target.value)}
              inputMode="numeric"
              autoComplete="one-time-code"
              autoFocus
            />
          </label>
        )
      ) : null}

      {step === "password" ? (
        <label className="field">
          <span>Пароль 2FA</span>
          <input
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            autoComplete="current-password"
            autoFocus
          />
        </label>
      ) : null}

      {step !== "done" && !waitingCodeSend ? (
        <button className="primary" type="button" disabled={busy || (step === "code" && !loginId)} onClick={() => actionRef.current()}>
          {busy ? "Подождите…" : LABELS[step]}
        </button>
      ) : null}
    </div>
  );
}
