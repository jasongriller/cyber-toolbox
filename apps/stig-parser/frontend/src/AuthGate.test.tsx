import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { axe } from 'jest-axe';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import AuthGate from './AuthGate';

const authMock = vi.hoisted(() => ({
  useAuth: vi.fn(),
  noAuth: false,
}));

vi.mock('./contexts/AuthContext', () => ({
  useAuth: authMock.useAuth,
  get isNoAuthMode() {
    return authMock.noAuth;
  },
}));

interface FakeAuthState {
  email: string | null;
  loading: boolean;
  requiresNewPassword: boolean;
  mfaStage: 'none' | 'setup' | 'code';
  mfaSecret: string | null;
  login: ReturnType<typeof vi.fn>;
  completeNewPassword: ReturnType<typeof vi.fn>;
  submitTotpCode: ReturnType<typeof vi.fn>;
  logout: ReturnType<typeof vi.fn>;
}

function mockAuth(overrides: Partial<FakeAuthState> = {}) {
  const state: FakeAuthState = {
    email: null,
    loading: false,
    requiresNewPassword: false,
    mfaStage: 'none',
    mfaSecret: null,
    login: vi.fn().mockResolvedValue(undefined),
    completeNewPassword: vi.fn().mockResolvedValue(undefined),
    submitTotpCode: vi.fn().mockResolvedValue(undefined),
    logout: vi.fn(),
    ...overrides,
  };
  authMock.useAuth.mockReturnValue(state);
  return state;
}

describe('AuthGate', () => {
  beforeEach(() => {
    authMock.useAuth.mockReset();
    authMock.noAuth = false;
  });

  it('shows the toolbox bar with the app content in NO_AUTH mode, without sign-out', () => {
    authMock.noAuth = true;
    mockAuth();
    render(<AuthGate><p>app content</p></AuthGate>);

    expect(screen.getByRole('navigation', { name: /cyber toolbox/i })).toBeInTheDocument();
    expect(screen.getByText('app content')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /sign out/i })).not.toBeInTheDocument();
  });

  it('links home and to the sibling tool without dropping the current path prefix', () => {
    authMock.noAuth = true;
    mockAuth();
    render(<AuthGate><p>app content</p></AuthGate>);

    // jsdom serves from "/", the same shape as local dev; deployed, the same
    // derivation keeps the API Gateway stage segment (the documented 403 trap).
    expect(screen.getByRole('link', { name: /cyber toolbox/i })).toHaveAttribute('href', '/');
    expect(screen.getByRole('link', { name: /rmf migrator/i })).toHaveAttribute(
      'href',
      '/rmf/index.html',
    );
  });

  it('shows the bar with a loading note during session restore, not a blank page', () => {
    mockAuth({ loading: true });
    render(<AuthGate><p>app content</p></AuthGate>);

    expect(screen.getByRole('navigation', { name: /cyber toolbox/i })).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent(/loading/i);
    expect(screen.queryByText('app content')).not.toBeInTheDocument();
  });

  it('shows the bar above the login form when signed out', () => {
    mockAuth();
    render(<AuthGate><p>app content</p></AuthGate>);

    expect(screen.getByRole('navigation', { name: /cyber toolbox/i })).toBeInTheDocument();
    expect(screen.getByLabelText(/email/i)).toBeInTheDocument();
    expect(screen.queryByText('app content')).not.toBeInTheDocument();
  });

  it('shows the signed-in email and signs out from the bar', async () => {
    const auth = mockAuth({ email: 'user@example.mil' });
    render(<AuthGate><p>app content</p></AuthGate>);

    expect(screen.getByText('user@example.mil')).toBeInTheDocument();
    expect(screen.getByText('app content')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /sign out/i }));
    expect(auth.logout).toHaveBeenCalled();
  });

  it('the signed-in bar has no axe violations', async () => {
    mockAuth({ email: 'user@example.mil' });
    const { container } = render(<AuthGate><p>app content</p></AuthGate>);
    expect(await axe(container)).toHaveNoViolations();
  });
});
