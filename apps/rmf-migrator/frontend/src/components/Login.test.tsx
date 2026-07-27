import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { axe } from 'jest-axe';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import Login from './Login';

const mockUseAuth = vi.hoisted(() => vi.fn());

vi.mock('../contexts/AuthContext', () => ({
  useAuth: mockUseAuth,
}));

type MfaStage = 'none' | 'setup' | 'code';

interface FakeAuthState {
  email: string | null;
  loading: boolean;
  requiresNewPassword: boolean;
  mfaStage: MfaStage;
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
  mockUseAuth.mockReturnValue(state);
  return state;
}

describe('Login', () => {
  beforeEach(() => {
    mockUseAuth.mockReset();
  });

  it('renders the email/password sign-in form', () => {
    mockAuth();
    render(<Login />);

    expect(screen.getByLabelText(/email/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/^password$/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /sign in/i })).toBeInTheDocument();
  });

  it('calls login with the typed credentials', async () => {
    const auth = mockAuth();
    render(<Login />);

    await userEvent.type(screen.getByLabelText(/email/i), 'user@example.mil');
    await userEvent.type(screen.getByLabelText(/^password$/i), 'Password123!');
    await userEvent.click(screen.getByRole('button', { name: /sign in/i }));

    expect(auth.login).toHaveBeenCalledWith('user@example.mil', 'Password123!');
  });

  it('(negative) surfaces a failed login as an announced error, not a silent no-op', async () => {
    mockAuth({
      login: vi.fn().mockRejectedValue(new Error('Incorrect username or password.')),
    });
    render(<Login />);

    await userEvent.type(screen.getByLabelText(/email/i), 'user@example.mil');
    await userEvent.type(screen.getByLabelText(/^password$/i), 'wrong-password');
    await userEvent.click(screen.getByRole('button', { name: /sign in/i }));

    // role="alert" is what makes assistive tech announce this — a plain <p>
    // update on a failed submit is otherwise easy to miss.
    expect(await screen.findByRole('alert')).toHaveTextContent(
      /incorrect username or password/i,
    );
  });

  it('switches to the set-password view and states the pool password policy', () => {
    mockAuth({ requiresNewPassword: true });
    render(<Login />);

    expect(screen.getByLabelText(/^new password$/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/confirm new password/i)).toBeInTheDocument();

    // Must match the pool policy: min 12, upper, lower, number, symbol.
    expect(screen.getByText(/12/)).toBeInTheDocument();
    expect(screen.getByText(/uppercase/i)).toBeInTheDocument();
    expect(screen.getByText(/lowercase/i)).toBeInTheDocument();
    expect(screen.getByText(/number/i)).toBeInTheDocument();
    expect(screen.getByText(/symbol/i)).toBeInTheDocument();

    // Weak password: submit stays disabled — a rejected weak password must not
    // be silently swallowed by a click that does nothing.
    expect(screen.getByRole('button', { name: /set password/i })).toBeDisabled();
  });

  it('enables the set-password submit only once the new password meets the policy and matches', async () => {
    mockAuth({ requiresNewPassword: true });
    render(<Login />);

    const submit = screen.getByRole('button', { name: /set password/i });
    await userEvent.type(screen.getByLabelText(/^new password$/i), 'weak');
    expect(submit).toBeDisabled();

    await userEvent.type(screen.getByLabelText(/^new password$/i), 'orGoodPass123!');
    expect(submit).toBeDisabled(); // confirm field still empty

    await userEvent.type(screen.getByLabelText(/confirm new password/i), 'weakorGoodPass123!');
    expect(submit).toBeEnabled();
  });

  it('shows the TOTP enroll view with a copyable secret and an otpauth link', () => {
    mockAuth({ mfaStage: 'setup', mfaSecret: 'JBSWY3DPEHPK3PXP' });
    render(<Login />);

    // Selectable/copyable: a real value on a text field, not just static prose.
    expect(screen.getByDisplayValue('JBSWY3DPEHPK3PXP')).toBeInTheDocument();

    const link = screen.getByRole('link');
    const href = link.getAttribute('href') ?? '';
    expect(href).toMatch(/^otpauth:\/\/totp\/cyber-toolbox:/);
    expect(href).toContain('secret=JBSWY3DPEHPK3PXP');
    expect(href).toContain('issuer=cyber-toolbox');

    expect(screen.getByLabelText(/6-digit/i)).toBeInTheDocument();
  });

  it('submits the enrollment code via submitTotpCode', async () => {
    const auth = mockAuth({ mfaStage: 'setup', mfaSecret: 'JBSWY3DPEHPK3PXP' });
    render(<Login />);

    await userEvent.type(screen.getByLabelText(/6-digit/i), '654321');
    await userEvent.click(screen.getByRole('button', { name: /verify/i }));

    expect(auth.submitTotpCode).toHaveBeenCalledWith('654321');
  });

  it('shows the TOTP code-challenge view with no secret on screen', () => {
    mockAuth({ mfaStage: 'code' });
    render(<Login />);

    expect(screen.getByLabelText(/6-digit/i)).toBeInTheDocument();
    expect(screen.queryByLabelText(/secret/i)).not.toBeInTheDocument();
  });

  it('submits the challenge code via submitTotpCode', async () => {
    const auth = mockAuth({ mfaStage: 'code' });
    render(<Login />);

    await userEvent.type(screen.getByLabelText(/6-digit/i), '123456');
    await userEvent.click(screen.getByRole('button', { name: /verify/i }));

    expect(auth.submitTotpCode).toHaveBeenCalledWith('123456');
  });

  describe('accessibility', () => {
    it('the sign-in view has no axe violations', async () => {
      mockAuth();
      const { container } = render(<Login />);
      expect(await axe(container)).toHaveNoViolations();
    });

    it('the sign-in view with an alert error shown has no axe violations', async () => {
      mockAuth({
        login: vi.fn().mockRejectedValue(new Error('Incorrect username or password.')),
      });
      const { container } = render(<Login />);

      await userEvent.type(screen.getByLabelText(/email/i), 'user@example.mil');
      await userEvent.type(screen.getByLabelText(/^password$/i), 'wrong-password');
      await userEvent.click(screen.getByRole('button', { name: /sign in/i }));
      await screen.findByRole('alert');

      expect(await axe(container)).toHaveNoViolations();
    });

    it('the set-password view has no axe violations', async () => {
      mockAuth({ requiresNewPassword: true });
      const { container } = render(<Login />);
      expect(await axe(container)).toHaveNoViolations();
    });

    it('the TOTP enroll view has no axe violations', async () => {
      mockAuth({ mfaStage: 'setup', mfaSecret: 'JBSWY3DPEHPK3PXP' });
      const { container } = render(<Login />);
      expect(await axe(container)).toHaveNoViolations();
    });

    it('the TOTP code view has no axe violations', async () => {
      mockAuth({ mfaStage: 'code' });
      const { container } = render(<Login />);
      expect(await axe(container)).toHaveNoViolations();
    });
  });
});
