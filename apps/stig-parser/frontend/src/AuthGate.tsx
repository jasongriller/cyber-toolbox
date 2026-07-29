import type { ReactNode } from 'react';
import Login from './components/Login';
import { isNoAuthMode, useAuth } from './contexts/AuthContext';

// The portal and the sibling tool live above this app's own path segment.
// Deriving the prefix from the live pathname keeps the API Gateway stage
// segment intact — a hardcoded "/" would drop it and 403, the blank-page trap
// documented in infra/modules/api/landing/index.html. Local dev has no stage,
// so this degrades to "/".
const stagePrefix = window.location.pathname.replace(/\/(stig|rmf)(\/.*)?$/, '/');

function ToolboxBar({ right }: { right?: ReactNode }) {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        gap: '1rem',
        padding: '.5rem 1rem',
        background: 'var(--color-surface)',
        borderBottom: '1px solid var(--color-border)',
        fontSize: 'var(--text-small)',
        color: 'var(--color-muted)',
      }}
    >
      <nav
        aria-label="Cyber Toolbox"
        style={{ display: 'flex', alignItems: 'center', gap: '.75rem' }}
      >
        <a href={stagePrefix}>
          <span aria-hidden="true">⌂ </span>Cyber Toolbox
        </a>
        <span aria-hidden="true">·</span>
        <a href={`${stagePrefix}rmf/index.html`}>
          RMF Migrator <span aria-hidden="true">→</span>
        </a>
      </nav>
      {right}
    </div>
  );
}

/**
 * Gates the app behind Cognito sign-in and owns the toolbox bar: home and
 * cross-tool links on the left, session controls on the right. The bar renders
 * in every mode, including on the login screen — a signed-out user can always
 * get back to the portal. NO_AUTH deployments (no VITE_COGNITO_*, i.e. every
 * local/test run today) skip the gate itself and have no session to show, so
 * the bar's right half stays empty.
 */
export default function AuthGate({ children }: { children: ReactNode }) {
  const { email, loading, logout } = useAuth();

  if (isNoAuthMode) {
    return (
      <>
        <ToolboxBar />
        {children}
      </>
    );
  }

  // Session-restore check in flight — don't flash the login form only to swap
  // it for the app a moment later, but keep the bar up and say what's
  // happening instead of painting a blank page.
  if (loading) {
    return (
      <>
        <ToolboxBar />
        <div className="container">
          <p role="status">Loading…</p>
        </div>
      </>
    );
  }

  if (!email) {
    return (
      <>
        <ToolboxBar />
        <Login />
      </>
    );
  }

  return (
    <>
      <ToolboxBar
        right={
          <div style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
            <span>{email}</span>
            <button type="button" className="btn btn-secondary" onClick={logout}>
              Sign out
            </button>
          </div>
        }
      />
      {children}
    </>
  );
}
