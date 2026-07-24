import type { ReactNode } from 'react';
import Login from './components/Login';
import { useAuth } from './contexts/AuthContext';

/**
 * Gates the app behind Cognito sign-in. NO_AUTH deployments (no VITE_COGNITO_*,
 * i.e. every local/test run) never see any of this — loading resolves false and
 * email is pre-populated, so children render immediately with no header.
 */
export default function AuthGate({ children }: { children: ReactNode }) {
  const { email, loading, logout } = useAuth();

  // Session-restore check in flight — nothing meaningful to paint yet, and
  // flashing the login form only to swap it for the app a moment later is
  // worse than a brief blank frame.
  if (loading) return null;

  if (!email) return <Login />;

  return (
    <>
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
        <span>{email}</span>
        <button type="button" className="btn btn-secondary" onClick={logout}>
          Sign out
        </button>
      </div>
      {children}
    </>
  );
}
