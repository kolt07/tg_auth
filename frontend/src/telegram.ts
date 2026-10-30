export type TelegramThemeParams = {
  bg_color?: string;
  text_color?: string;
  hint_color?: string;
  link_color?: string;
  button_color?: string;
  button_text_color?: string;
  secondary_bg_color?: string;
};

export type TelegramWebApp = {
  initData: string;
  ready: () => void;
  expand: () => void;
  close: () => void;
  disableVerticalSwipes?: () => void;
  setHeaderColor?: (color: string) => void;
  setBackgroundColor?: (color: string) => void;
  themeParams: TelegramThemeParams;
  colorScheme?: "light" | "dark";
  MainButton: {
    text: string;
    color: string;
    textColor: string;
    isVisible: boolean;
    isActive: boolean;
    isProgressVisible: boolean;
    setText: (text: string) => void;
    show: () => void;
    hide: () => void;
    enable: () => void;
    disable: () => void;
    showProgress: (leaveActive?: boolean) => void;
    hideProgress: () => void;
    onClick: (cb: () => void) => void;
    offClick: (cb: () => void) => void;
  };
  HapticFeedback?: {
    notificationOccurred: (type: "error" | "success" | "warning") => void;
  };
  requestContact?: (callback?: (sent: boolean) => void) => void;
};

declare global {
  interface Window {
    Telegram?: { WebApp?: TelegramWebApp };
  }
}

export function getWebApp(): TelegramWebApp | null {
  return window.Telegram?.WebApp ?? null;
}

export function isMiniApp(): boolean {
  const app = getWebApp();
  return Boolean(app && (app.initData || app.expand));
}

export function initMiniApp(): TelegramWebApp | null {
  const app = getWebApp();
  if (!app) return null;
  app.ready();
  app.expand();
  app.disableVerticalSwipes?.();
  applyTheme(app);
  return app;
}

export function applyTheme(app: TelegramWebApp = getWebApp()!): void {
  if (!app) return;
  const root = document.documentElement;
  const theme = app.themeParams;
  if (theme.bg_color) root.style.setProperty("--bg", theme.bg_color);
  if (theme.secondary_bg_color) root.style.setProperty("--card", theme.secondary_bg_color);
  if (theme.text_color) root.style.setProperty("--ink", theme.text_color);
  if (theme.hint_color) root.style.setProperty("--muted", theme.hint_color);
  if (theme.button_color) root.style.setProperty("--accent", theme.button_color);
  if (theme.button_text_color) root.style.setProperty("--accent-ink", theme.button_text_color);
  root.style.setProperty("--line", theme.hint_color ? `${theme.hint_color}33` : "transparent");
  if (theme.bg_color) {
    app.setHeaderColor?.(theme.bg_color);
    app.setBackgroundColor?.(theme.bg_color);
    document.body.style.background = theme.bg_color;
  }
}
