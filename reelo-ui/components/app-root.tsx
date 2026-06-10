"use client";

// ===== Root App: client-side routing + theme + ambient background =====
// Ported from the prototype's app.jsx. The in-prototype "Tweaks panel" was a
// prototyping aid (not part of the product), so it is intentionally dropped;
// the light/dark toggle lives in the top bar. Routing stays as internal state
// to match the prototype 1:1 — swap for the Next.js router when you add real URLs.

import React from "react";
import { Icon } from "./ui";
import { Sidebar, Topbar } from "./shell";
import { Dashboard } from "@/screens/dashboard";
import { WizardScreen } from "@/screens/wizard";
import { SetupScreen } from "@/screens/setup";
import { StyleScreen } from "@/screens/style";
import { WorkspaceScreen } from "@/screens/workspace";
import { ImageSelectScreen } from "@/screens/image-select";
import { ReviewScreen } from "@/screens/review";
import { ProjectScreen } from "@/screens/project";
import { SettingsScreen } from "@/screens/settings";
import type { Nav, Route } from "@/lib/data";
import { getMe, loginUrl, logout, type Me } from "@/lib/api";

function Toast({ toast }: { toast: string | null }) {
  if (!toast) return null;
  return (
    <div
      className="fade-up"
      style={{
        position: "fixed",
        bottom: 24,
        left: "50%",
        transform: "translateX(-50%)",
        zIndex: 100,
        background: "var(--text)",
        color: "var(--bg)",
        padding: "12px 18px",
        borderRadius: 12,
        fontWeight: 600,
        fontSize: 14,
        boxShadow: "var(--shadow-lg)",
        display: "flex",
        alignItems: "center",
        gap: 10,
      }}
    >
      <Icon name="check-circle-2" size={18} style={{ color: "#4ade80" }} /> {toast}
    </div>
  );
}

// Minimal Google-login gate. Auth state has three values:
//   undefined = still checking, null = logged out, Me = logged in.
// Set NEXT_PUBLIC_REQUIRE_AUTH=false to skip the gate (useful for the mock-data
// demo when no backend is running).
const REQUIRE_AUTH =
  (typeof process !== "undefined" && process.env.NEXT_PUBLIC_REQUIRE_AUTH) !== "false";

function LoginScreen() {
  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 18,
        padding: 24,
        textAlign: "center",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <Icon name="clapperboard" size={28} style={{ color: "var(--brand)" }} />
        <h1 style={{ fontSize: 26, fontWeight: 800 }}>Reelo Studio</h1>
      </div>
      <p className="muted" style={{ fontSize: 15, maxWidth: 420 }}>
        Đăng nhập bằng tài khoản Google để tạo và quản lý series video của bạn.
      </p>
      <a className="btn btn-primary btn-lg" href={loginUrl()}>
        <Icon name="log-in" size={18} /> Đăng nhập với Google
      </a>
    </div>
  );
}

const SCREENS: Record<Route["name"], React.ComponentType<{ nav: Nav; route: Route }>> = {
  dashboard: Dashboard as React.ComponentType<{ nav: Nav; route: Route }>,
  wizard: WizardScreen as React.ComponentType<{ nav: Nav; route: Route }>,
  setup: SetupScreen,
  style: StyleScreen,
  workspace: WorkspaceScreen,
  "image-select": ImageSelectScreen,
  review: ReviewScreen,
  project: ProjectScreen,
  settings: SettingsScreen,
};

export function App() {
  const [dark, setDark] = React.useState(false);
  const [route, setRoute] = React.useState<Route>({ name: "dashboard" });
  const [toast, setToast] = React.useState<string | null>(null);
  const [user, setUser] = React.useState<Me | null | undefined>(
    REQUIRE_AUTH ? undefined : null,
  );
  const contentRef = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
  }, [dark]);

  // Auth check on mount (skipped when the gate is disabled for the mock demo).
  React.useEffect(() => {
    if (!REQUIRE_AUTH) return;
    getMe()
      .then(setUser)
      .catch(() => setUser(null)); // backend unreachable → treat as logged out
  }, []);

  const nav: Nav = (r) => {
    if (r.toast) {
      setToast(r.toast);
      setTimeout(() => setToast(null), 2800);
    }
    setRoute(r);
    if (contentRef.current) contentRef.current.scrollTop = 0;
  };

  // Auth gate.
  if (REQUIRE_AUTH && user === undefined) {
    return (
      <div style={{ minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center" }}>
        <Icon name="loader" size={28} style={{ color: "var(--brand)" }} />
      </div>
    );
  }
  if (REQUIRE_AUTH && user === null) {
    return <LoginScreen />;
  }

  const Screen = SCREENS[route.name] || Dashboard;

  const onLogout = async () => {
    try {
      await logout();
    } finally {
      setUser(null);
    }
  };

  return (
    <>
      <div className="app-bg" aria-hidden="true">
        <div className="bg-blob bg-b1" />
        <div className="bg-blob bg-b2" />
        <div className="bg-blob bg-b3" />
      </div>
      <div className="shell">
        <Sidebar route={route} nav={nav} />
        <div className="main">
          <Topbar
            route={route}
            nav={nav}
            theme={dark ? "dark" : "light"}
            onToggleTheme={() => setDark((d) => !d)}
            user={user}
            onLogout={onLogout}
          />
          <div className="content" ref={contentRef}>
            <Screen
              key={route.name + (route.series?.id || "") + (route.episode?.id || "")}
              nav={nav}
              route={route}
            />
          </div>
        </div>

        <Toast toast={toast} />
      </div>
    </>
  );
}
