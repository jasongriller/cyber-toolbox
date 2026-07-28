import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { useAuth } from "../contexts/AuthContext";

type View = "login" | "new-password" | "enroll" | "code";

// Mirrors the Cognito pool's password_policy (min 12, all four character classes).
const PW_REQS = [
  { key: "len", label: "12+ characters", test: (p: string) => p.length >= 12 },
  { key: "upper", label: "Uppercase letter", test: (p: string) => /[A-Z]/.test(p) },
  { key: "lower", label: "Lowercase letter", test: (p: string) => /[a-z]/.test(p) },
  { key: "num", label: "Number", test: (p: string) => /[0-9]/.test(p) },
  { key: "sym", label: "Symbol (!@#$ …)", test: (p: string) => /[^A-Za-z0-9]/.test(p) },
];

// Promise rejections that are transitions, not failures — the view-switching
// effect below reacts to these; the form handlers must not surface them as
// user-facing errors.
const SIGNAL_ERRORS = new Set(["new-password-required", "mfa-setup-required", "totp-required"]);

export default function Login() {
  const {
    login,
    requiresNewPassword,
    completeNewPassword,
    mfaStage,
    mfaSecret,
    submitTotpCode,
    resetChallenge,
  } = useAuth();

  const [view, setView] = useState<View>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [newPw, setNewPw] = useState("");
  const [confirmPw, setConfirmPw] = useState("");
  const [totpCode, setTotpCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // mfaStage takes priority over requiresNewPassword: an admin-created account's
  // first login can chain NEW_PASSWORD_REQUIRED straight into MFA_SETUP, and by
  // the time that happens AuthContext has already dropped requiresNewPassword.
  useEffect(() => {
    if (mfaStage === "setup") setView("enroll");
    else if (mfaStage === "code") setView("code");
    else if (requiresNewPassword) setView("new-password");
  }, [requiresNewPassword, mfaStage]);

  const reqs = PW_REQS.map((r) => ({ ...r, met: r.test(newPw) }));
  const allMet = reqs.every((r) => r.met);

  const otpauthUri =
    `otpauth://totp/cyber-toolbox:${encodeURIComponent(email)}?secret=${mfaSecret ?? ""}&issuer=cyber-toolbox`;

  const onLogin = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await login(email, password);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Login failed";
      if (!SIGNAL_ERRORS.has(msg)) setError(msg);
    } finally {
      setBusy(false);
    }
  };

  const onNewPassword = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    if (newPw !== confirmPw) {
      setError("Passwords do not match");
      return;
    }
    if (!allMet) {
      setError("Password does not meet requirements");
      return;
    }
    setBusy(true);
    try {
      await completeNewPassword(newPw);
      // onSuccess (or a chained mfaSetup/totpRequired) drives what renders next.
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Failed to set password";
      if (!SIGNAL_ERRORS.has(msg)) setError(msg);
    } finally {
      setBusy(false);
    }
  };

  const onTotp = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await submitTotpCode(totpCode);
      setTotpCode("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Verification failed");
    } finally {
      setBusy(false);
    }
  };

  // Escape hatch for every challenge view — a mistyped email discovered at the
  // code prompt used to require a full page reload. The challenge fields are
  // cleared, but not email/password: the usual reason to bail is fixing a typo.
  const backToSignIn = () => {
    resetChallenge();
    setView("login");
    setError(null);
    setNewPw("");
    setConfirmPw("");
    setTotpCode("");
  };

  const heading =
    view === "login" ? "Sign in"
    : view === "new-password" ? "Set your password"
    : view === "enroll" ? "Set up an authenticator app"
    : "Enter your authentication code";

  return (
    <div className="app">
      <div className="panel" style={{ maxWidth: 400, margin: "4rem auto", textAlign: "left" }}>
        <h1 style={{ marginBottom: 18 }}>{heading}</h1>

        {view === "new-password" && (
          <p className="muted" style={{ marginBottom: 16, lineHeight: 1.55 }}>
            Your account was created by an administrator. Choose a permanent password to continue.
          </p>
        )}

        {view === "enroll" && (
          <p className="muted" style={{ marginBottom: 16, lineHeight: 1.55 }}>
            Add this account to an authenticator app (Google Authenticator, Authy, 1Password…) by
            entering the secret key below or opening the setup link, then enter the 6-digit code
            it generates.
          </p>
        )}

        {view === "code" && (
          <p className="muted" style={{ marginBottom: 16, lineHeight: 1.55 }}>
            Enter the 6-digit code from your authenticator app.
          </p>
        )}

        {view === "login" && (
          <form onSubmit={onLogin} style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <label htmlFor="login-email" className="visually-hidden">Email</label>
            <input
              id="login-email"
              type="email"
              placeholder="Email"
              className="field"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              autoFocus
            />
            <label htmlFor="login-password" className="visually-hidden">Password</label>
            <input
              id="login-password"
              type="password"
              placeholder="Password"
              className="field"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
            <button type="submit" className="btn btn--accent" disabled={busy}>
              {busy ? "Signing in…" : "Sign in"}
            </button>
            {error ? (
              <p role="alert" className="banner banner--error">{error}</p>
            ) : null}
          </form>
        )}

        {view === "new-password" && (
          <form onSubmit={onNewPassword} style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <label htmlFor="new-password" className="visually-hidden">New password</label>
            <input
              id="new-password"
              type="password"
              placeholder="New password"
              className="field"
              value={newPw}
              onChange={(e) => setNewPw(e.target.value)}
              required
              autoFocus
            />
            <label htmlFor="confirm-new-password" className="visually-hidden">Confirm new password</label>
            <input
              id="confirm-new-password"
              type="password"
              placeholder="Confirm new password"
              className="field"
              value={confirmPw}
              onChange={(e) => setConfirmPw(e.target.value)}
              required
            />
            <div
              style={{
                display: "flex",
                flexDirection: "column",
                gap: 4,
                padding: "8px 10px",
                background: "var(--inset)",
                borderRadius: "var(--radius)",
                border: "1px solid var(--border)",
              }}
            >
              {reqs.map((r) => (
                <div
                  key={r.key}
                  style={{
                    fontSize: 11.5,
                    color: r.met ? "var(--ok)" : "var(--text-mute)",
                  }}
                >
                  {r.met ? "✓" : "○"} {r.label}
                </div>
              ))}
            </div>
            <button
              type="submit"
              className="btn btn--accent"
              disabled={busy || !allMet || confirmPw.length === 0}
            >
              {busy ? "Saving…" : "Set password & sign in"}
            </button>
            <button
              type="button"
              className="btn btn--ghost"
              disabled={busy}
              onClick={backToSignIn}
            >
              ← Back to sign in
            </button>
            {error ? (
              <p role="alert" className="banner banner--error">{error}</p>
            ) : null}
          </form>
        )}

        {view === "enroll" && (
          <form onSubmit={onTotp} style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <label htmlFor="totp-secret" className="visually-hidden">Secret key</label>
            <input
              id="totp-secret"
              type="text"
              readOnly
              className="field mono"
              value={mfaSecret ?? ""}
              onFocus={(e) => e.currentTarget.select()}
              style={{ letterSpacing: ".03em" }}
            />
            <a href={otpauthUri} style={{ fontSize: 12.5, wordBreak: "break-all" }}>
              Open setup link in an authenticator app
            </a>
            <label htmlFor="totp-code" className="visually-hidden">6-digit authentication code</label>
            <input
              id="totp-code"
              type="text"
              inputMode="numeric"
              pattern="[0-9]{6}"
              maxLength={6}
              placeholder="123456"
              className="field"
              value={totpCode}
              onChange={(e) => setTotpCode(e.target.value)}
              required
            />
            <button type="submit" className="btn btn--accent" disabled={busy || totpCode.length !== 6}>
              {busy ? "Verifying…" : "Verify & finish setup"}
            </button>
            <button
              type="button"
              className="btn btn--ghost"
              disabled={busy}
              onClick={backToSignIn}
            >
              ← Back to sign in
            </button>
            {error ? (
              <p role="alert" className="banner banner--error">{error}</p>
            ) : null}
          </form>
        )}

        {view === "code" && (
          <form onSubmit={onTotp} style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <label htmlFor="totp-code" className="visually-hidden">6-digit authentication code</label>
            <input
              id="totp-code"
              type="text"
              inputMode="numeric"
              pattern="[0-9]{6}"
              maxLength={6}
              placeholder="123456"
              className="field"
              value={totpCode}
              onChange={(e) => setTotpCode(e.target.value)}
              required
              autoFocus
            />
            <button type="submit" className="btn btn--accent" disabled={busy || totpCode.length !== 6}>
              {busy ? "Verifying…" : "Verify code"}
            </button>
            <button
              type="button"
              className="btn btn--ghost"
              disabled={busy}
              onClick={backToSignIn}
            >
              ← Back to sign in
            </button>
            {error ? (
              <p role="alert" className="banner banner--error">{error}</p>
            ) : null}
          </form>
        )}
      </div>
    </div>
  );
}
