import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

// A fake CognitoUser instance, shaped just enough for AuthContext to drive it.
// Each SDK method is its own vi.fn() so tests can assert call args directly.
interface FakeCognitoUser {
  username: string;
  getUsername: () => string;
  authenticateUser: ReturnType<typeof vi.fn>;
  completeNewPasswordChallenge: ReturnType<typeof vi.fn>;
  associateSoftwareToken: ReturnType<typeof vi.fn>;
  verifySoftwareToken: ReturnType<typeof vi.fn>;
  sendMFACode: ReturnType<typeof vi.fn>;
  getSession: ReturnType<typeof vi.fn>;
  signOut: ReturnType<typeof vi.fn>;
}

type Behavior =
  | 'success'
  | 'failure'
  | 'newPasswordRequired'
  | 'mfaSetup'
  | 'totpRequired'
  | 'mfaRequired'
  | 'selectMFAType'
  | 'customChallenge';

/** What getCurrentUser() returns for session-restore-on-mount and getIdToken tests —
 *  a user "already signed in" (e.g. from a persisted session), independent of
 *  anything constructed via login(). */
interface FakeCurrentUser {
  getUsername: () => string;
  getSession: ReturnType<typeof vi.fn>;
  signOut: ReturnType<typeof vi.fn>;
}

// vi.hoisted: vi.mock factories below run before this file's own top-level code,
// so any state they need to read/write has to be created here to survive the hoist.
const mocks = vi.hoisted(() => ({
  cognitoConfig: {
    userPoolId: '',
    clientId: '',
    region: '',
    endpoint: '',
  },
  authBehavior: 'success' as Behavior,
  completeBehavior: 'success' as Behavior,
  /** Only sendMFACode/verifySoftwareToken honor this — everything else in the
   *  challenge chain is covered by authBehavior/completeBehavior. */
  totpBehavior: 'success' as 'success' | 'failure',
  secret: 'JBSWY3DPEHPK3PXP',
  createdUsers: [] as FakeCognitoUser[],
  poolCtorArgs: [] as unknown[],
  /** What CognitoUserPool#getCurrentUser() returns; null unless a test opts in. */
  currentUser: null as FakeCurrentUser | null,
}));

vi.mock('../config/cognito', () => ({
  cognitoConfig: mocks.cognitoConfig,
}));

vi.mock('amazon-cognito-identity-js', () => {
  type Callbacks = Record<string, ((...args: unknown[]) => void) | undefined>;

  function dispatch(behavior: Behavior, callbacks: Callbacks) {
    if (behavior === 'success') return callbacks.onSuccess?.();
    if (behavior === 'failure') {
      return callbacks.onFailure?.(new Error('Incorrect username or password.'));
    }
    if (behavior === 'newPasswordRequired') return callbacks.newPasswordRequired?.();
    if (behavior === 'mfaSetup') return callbacks.mfaSetup?.();
    if (behavior === 'totpRequired') return callbacks.totpRequired?.();
    // Challenge types this app doesn't implement — dispatched with the same
    // (challengeName, challengeParameters) shape the real SDK uses, to prove
    // AuthContext's handlers don't depend on ignoring those args.
    if (behavior === 'mfaRequired') return callbacks.mfaRequired?.('SMS_MFA', {});
    if (behavior === 'selectMFAType') return callbacks.selectMFAType?.('SELECT_MFA_TYPE', {});
    if (behavior === 'customChallenge') return callbacks.customChallenge?.({});
    return undefined;
  }

  class MockCognitoUser implements FakeCognitoUser {
    username: string;

    constructor({ Username }: { Username: string }) {
      this.username = Username;
      mocks.createdUsers.push(this);
    }

    getUsername() {
      return this.username;
    }

    authenticateUser = vi.fn((_details: unknown, callbacks: Callbacks) => {
      dispatch(mocks.authBehavior, callbacks);
    });

    completeNewPasswordChallenge = vi.fn(
      (_newPassword: string, _attrs: unknown, callbacks: Callbacks) => {
        dispatch(mocks.completeBehavior, callbacks);
      },
    );

    associateSoftwareToken = vi.fn(
      (callbacks: { associateSecretCode: (s: string) => void; onFailure: (e: Error) => void }) => {
        callbacks.associateSecretCode(mocks.secret);
      },
    );

    verifySoftwareToken = vi.fn(
      (_code: string, _name: string, callbacks: { onSuccess: () => void; onFailure: (e: Error) => void }) => {
        if (mocks.totpBehavior === 'failure') {
          return callbacks.onFailure(new Error('Invalid code.'));
        }
        callbacks.onSuccess();
      },
    );

    sendMFACode = vi.fn(
      (_code: string, callbacks: { onSuccess: () => void; onFailure: (e: Error) => void }) => {
        if (mocks.totpBehavior === 'failure') {
          return callbacks.onFailure(new Error('Invalid code.'));
        }
        callbacks.onSuccess();
      },
    );

    getSession = vi.fn((callback: (err: Error | null, session: unknown) => void) => {
      callback(null, {
        isValid: () => true,
        getIdToken: () => ({ getJwtToken: () => 'fake-id-token' }),
      });
    });

    signOut = vi.fn();
  }

  class MockCognitoUserPool {
    constructor(data: unknown) {
      mocks.poolCtorArgs.push(data);
    }

    getCurrentUser() {
      return mocks.currentUser;
    }
  }

  class MockAuthenticationDetails {
    constructor(public data: unknown) {}
  }

  return {
    CognitoUser: MockCognitoUser,
    CognitoUserPool: MockCognitoUserPool,
    AuthenticationDetails: MockAuthenticationDetails,
  };
});

/** Builds a `getCurrentUser()` result simulating an already-signed-in session
 *  (e.g. restored from persisted storage) — independent of anything the test
 *  constructs via login(). `session` selects what getSession() reports back. */
function fakeCurrentUser(
  username: string,
  session: { valid: boolean } | { error: Error },
): FakeCurrentUser {
  return {
    getUsername: () => username,
    getSession: vi.fn((callback: (err: Error | null, session: unknown) => void) => {
      if ('error' in session) return callback(session.error, null);
      callback(null, {
        isValid: () => session.valid,
        getIdToken: () => ({ getJwtToken: () => 'fake-id-token' }),
      });
    }),
    signOut: vi.fn(),
  };
}

function setPoolConfigured(configured: boolean) {
  mocks.cognitoConfig.userPoolId = configured ? 'us-gov-west-1_testpool' : '';
  mocks.cognitoConfig.clientId = configured ? 'test-client-id' : '';
  mocks.cognitoConfig.region = configured ? 'us-gov-west-1' : '';
  mocks.cognitoConfig.endpoint = configured ? 'https://cognito-idp-fips.us-gov-west-1.amazonaws.com' : '';
}

/** Forces a fresh evaluation of AuthContext's module-scope `userPool`/`NO_AUTH`,
 *  which are derived once from cognitoConfig at import time. */
async function freshAuthContext() {
  vi.resetModules();
  return import('./AuthContext');
}

describe('AuthContext', () => {
  beforeEach(() => {
    mocks.createdUsers.length = 0;
    mocks.poolCtorArgs.length = 0;
    mocks.authBehavior = 'success';
    mocks.completeBehavior = 'success';
    mocks.totpBehavior = 'success';
    mocks.currentUser = null;
  });

  describe('with a configured pool', () => {
    it('(a) authenticateUser onSuccess sets email', async () => {
      setPoolConfigured(true);
      const { AuthProvider, useAuth, isNoAuthMode } = await freshAuthContext();
      expect(isNoAuthMode).toBe(false);
      const { result } = renderHook(() => useAuth(), { wrapper: AuthProvider });
      await waitFor(() => expect(result.current.loading).toBe(false));

      await act(async () => {
        await result.current.login('user@example.mil', 'Password123!');
      });

      expect(result.current.email).toBe('user@example.mil');
      // The endpoint override (required in GovCloud) reached the pool ctor.
      expect(mocks.poolCtorArgs[0]).toMatchObject({
        UserPoolId: 'us-gov-west-1_testpool',
        ClientId: 'test-client-id',
        endpoint: 'https://cognito-idp-fips.us-gov-west-1.amazonaws.com',
      });
    });

    it('(b) newPasswordRequired sets requiresNewPassword; completeNewPassword reuses the same user', async () => {
      mocks.authBehavior = 'newPasswordRequired';
      setPoolConfigured(true);
      const { AuthProvider, useAuth } = await freshAuthContext();
      const { result } = renderHook(() => useAuth(), { wrapper: AuthProvider });
      await waitFor(() => expect(result.current.loading).toBe(false));

      await act(async () => {
        await result.current.login('user@example.mil', 'Password123!').catch(() => {});
      });

      expect(result.current.requiresNewPassword).toBe(true);
      expect(mocks.createdUsers).toHaveLength(1);

      await act(async () => {
        await result.current.completeNewPassword('NewPassword123!');
      });

      // No second CognitoUser was constructed — the challenge completed on the
      // same instance authenticateUser created.
      expect(mocks.createdUsers).toHaveLength(1);
      expect(mocks.createdUsers[0].completeNewPasswordChallenge).toHaveBeenCalledTimes(1);
      expect(result.current.email).toBe('user@example.mil');
      expect(result.current.requiresNewPassword).toBe(false);
    });

    it('(c) mfaSetup populates mfaSecret from associateSoftwareToken; submitTotpCode verifies and completes', async () => {
      mocks.authBehavior = 'mfaSetup';
      setPoolConfigured(true);
      const { AuthProvider, useAuth } = await freshAuthContext();
      const { result } = renderHook(() => useAuth(), { wrapper: AuthProvider });
      await waitFor(() => expect(result.current.loading).toBe(false));

      await act(async () => {
        await result.current.login('user@example.mil', 'Password123!').catch(() => {});
      });

      expect(result.current.mfaStage).toBe('setup');
      expect(result.current.mfaSecret).toBe(mocks.secret);
      // Must not be mistaken for the new-password view.
      expect(result.current.requiresNewPassword).toBe(false);

      await act(async () => {
        await result.current.submitTotpCode('654321');
      });

      const user = mocks.createdUsers[0];
      expect(user.verifySoftwareToken).toHaveBeenCalledWith(
        '654321',
        'cyber-toolbox',
        expect.anything(),
      );
      expect(result.current.email).toBe('user@example.mil');
      expect(result.current.mfaStage).toBe('none');
      expect(result.current.mfaSecret).toBeNull();
    });

    it('(d) totpRequired sets mfaStage code; submitTotpCode sends a SOFTWARE_TOKEN_MFA code', async () => {
      mocks.authBehavior = 'totpRequired';
      setPoolConfigured(true);
      const { AuthProvider, useAuth } = await freshAuthContext();
      const { result } = renderHook(() => useAuth(), { wrapper: AuthProvider });
      await waitFor(() => expect(result.current.loading).toBe(false));

      await act(async () => {
        await result.current.login('user@example.mil', 'Password123!').catch(() => {});
      });

      expect(result.current.mfaStage).toBe('code');
      expect(result.current.mfaSecret).toBeNull();

      await act(async () => {
        await result.current.submitTotpCode('123456');
      });

      const user = mocks.createdUsers[0];
      expect(user.sendMFACode).toHaveBeenCalledWith(
        '123456',
        expect.anything(),
        'SOFTWARE_TOKEN_MFA',
      );
      expect(result.current.email).toBe('user@example.mil');
      expect(result.current.mfaStage).toBe('none');
    });

    it('an admin-created account chains NEW_PASSWORD_REQUIRED into MFA_SETUP on the same instance', async () => {
      // completeNewPasswordChallenge's own callback object must carry mfaSetup/totpRequired too.
      mocks.authBehavior = 'newPasswordRequired';
      mocks.completeBehavior = 'mfaSetup';
      setPoolConfigured(true);
      const { AuthProvider, useAuth } = await freshAuthContext();
      const { result } = renderHook(() => useAuth(), { wrapper: AuthProvider });
      await waitFor(() => expect(result.current.loading).toBe(false));

      await act(async () => {
        await result.current.login('user@example.mil', 'Password123!').catch(() => {});
      });
      expect(result.current.requiresNewPassword).toBe(true);

      await act(async () => {
        await result.current.completeNewPassword('NewPassword123!').catch(() => {});
      });

      expect(mocks.createdUsers).toHaveLength(1);
      expect(result.current.mfaStage).toBe('setup');
      expect(result.current.mfaSecret).toBe(mocks.secret);
      // The chain moved past the password step; it must not still claim to need one.
      expect(result.current.requiresNewPassword).toBe(false);
    });

    it('(negative) a wrong password rejects login() with the SDK error message', async () => {
      mocks.authBehavior = 'failure';
      setPoolConfigured(true);
      const { AuthProvider, useAuth } = await freshAuthContext();
      const { result } = renderHook(() => useAuth(), { wrapper: AuthProvider });
      await waitFor(() => expect(result.current.loading).toBe(false));

      await expect(
        result.current.login('user@example.mil', 'wrong-password'),
      ).rejects.toThrow('Incorrect username or password.');

      expect(result.current.email).toBeNull();
    });

    it('(negative) a failed TOTP code rejects and keeps mfaStage at code so a retry is possible', async () => {
      mocks.authBehavior = 'totpRequired';
      setPoolConfigured(true);
      const { AuthProvider, useAuth } = await freshAuthContext();
      const { result } = renderHook(() => useAuth(), { wrapper: AuthProvider });
      await waitFor(() => expect(result.current.loading).toBe(false));

      await act(async () => {
        await result.current.login('user@example.mil', 'Password123!').catch(() => {});
      });
      expect(result.current.mfaStage).toBe('code');

      mocks.totpBehavior = 'failure';
      await expect(result.current.submitTotpCode('000000')).rejects.toThrow('Invalid code.');

      // Still mid-challenge, not signed in — the caller can retry.
      expect(result.current.mfaStage).toBe('code');
      expect(result.current.email).toBeNull();
      expect(mocks.createdUsers).toHaveLength(1);

      // Proof the pending user survived the failure: a retry on the same
      // instance (no second CognitoUser, no "No pending challenge" rejection)
      // succeeds outright.
      mocks.totpBehavior = 'success';
      await act(async () => {
        await result.current.submitTotpCode('123456');
      });
      expect(mocks.createdUsers).toHaveLength(1);
      expect(mocks.createdUsers[0].sendMFACode).toHaveBeenCalledTimes(2);
      expect(result.current.email).toBe('user@example.mil');
    });

    it('resetChallenge abandons a pending new-password challenge', async () => {
      mocks.authBehavior = 'newPasswordRequired';
      setPoolConfigured(true);
      const { AuthProvider, useAuth } = await freshAuthContext();
      const { result } = renderHook(() => useAuth(), { wrapper: AuthProvider });
      await waitFor(() => expect(result.current.loading).toBe(false));

      await act(async () => {
        await result.current.login('user@example.mil', 'Password123!').catch(() => {});
      });
      expect(result.current.requiresNewPassword).toBe(true);

      act(() => {
        result.current.resetChallenge();
      });

      expect(result.current.requiresNewPassword).toBe(false);
      expect(result.current.email).toBeNull();
      // The abandoned instance is gone — completing now fails loudly instead of
      // silently driving a stale user.
      await expect(result.current.completeNewPassword('NewPassword123!')).rejects.toThrow(
        'No pending challenge',
      );
    });

    it('resetChallenge abandons a pending MFA challenge including the issued secret', async () => {
      mocks.authBehavior = 'mfaSetup';
      setPoolConfigured(true);
      const { AuthProvider, useAuth } = await freshAuthContext();
      const { result } = renderHook(() => useAuth(), { wrapper: AuthProvider });
      await waitFor(() => expect(result.current.loading).toBe(false));

      await act(async () => {
        await result.current.login('user@example.mil', 'Password123!').catch(() => {});
      });
      expect(result.current.mfaStage).toBe('setup');
      expect(result.current.mfaSecret).toBe(mocks.secret);

      act(() => {
        result.current.resetChallenge();
      });

      expect(result.current.mfaStage).toBe('none');
      expect(result.current.mfaSecret).toBeNull();
      await expect(result.current.submitTotpCode('123456')).rejects.toThrow(
        'No pending challenge',
      );
    });

    it('(negative) an unsupported challenge (e.g. SMS MFA) rejects login() instead of hanging', async () => {
      mocks.authBehavior = 'mfaRequired';
      setPoolConfigured(true);
      const { AuthProvider, useAuth } = await freshAuthContext();
      const { result } = renderHook(() => useAuth(), { wrapper: AuthProvider });
      await waitFor(() => expect(result.current.loading).toBe(false));

      await expect(
        result.current.login('user@example.mil', 'Password123!'),
      ).rejects.toThrow('unsupported challenge: SMS_MFA');

      expect(result.current.email).toBeNull();
      expect(result.current.loading).toBe(false);
    });
  });

  describe('getIdToken with a configured pool', () => {
    it('resolves the raw JWT string on a valid session — no "Bearer " prefix', async () => {
      setPoolConfigured(true);
      mocks.currentUser = fakeCurrentUser('user@example.mil', { valid: true });
      const { getIdToken } = await freshAuthContext();

      await expect(getIdToken()).resolves.toBe('fake-id-token');
    });

    it('resolves null when getSession reports an error', async () => {
      setPoolConfigured(true);
      mocks.currentUser = fakeCurrentUser('user@example.mil', { error: new Error('network') });
      const { getIdToken } = await freshAuthContext();

      await expect(getIdToken()).resolves.toBeNull();
    });

    it('resolves null when the session is invalid', async () => {
      setPoolConfigured(true);
      mocks.currentUser = fakeCurrentUser('user@example.mil', { valid: false });
      const { getIdToken } = await freshAuthContext();

      await expect(getIdToken()).resolves.toBeNull();
    });
  });

  describe('session restore on mount', () => {
    it('a current user with a valid session sets email from getUsername()', async () => {
      setPoolConfigured(true);
      mocks.currentUser = fakeCurrentUser('restored@example.mil', { valid: true });
      const { AuthProvider, useAuth } = await freshAuthContext();
      const { result } = renderHook(() => useAuth(), { wrapper: AuthProvider });

      await waitFor(() => expect(result.current.loading).toBe(false));
      expect(result.current.email).toBe('restored@example.mil');
    });

    it('a current user with an invalid session stays signed out', async () => {
      setPoolConfigured(true);
      mocks.currentUser = fakeCurrentUser('restored@example.mil', { valid: false });
      const { AuthProvider, useAuth } = await freshAuthContext();
      const { result } = renderHook(() => useAuth(), { wrapper: AuthProvider });

      await waitFor(() => expect(result.current.loading).toBe(false));
      expect(result.current.email).toBeNull();
    });
  });

  describe('with no pool configured (NO_AUTH)', () => {
    it('(e) auto-signs-in, login() resolves immediately, and getIdToken() resolves null', async () => {
      setPoolConfigured(false);
      const { AuthProvider, useAuth, getIdToken, isNoAuthMode } = await freshAuthContext();
      expect(isNoAuthMode).toBe(true);
      const { result } = renderHook(() => useAuth(), { wrapper: AuthProvider });
      await waitFor(() => expect(result.current.loading).toBe(false));

      expect(result.current.email).toBe('dev@local');

      await act(async () => {
        await result.current.login('user@example.mil', 'whatever');
      });
      expect(result.current.email).toBe('user@example.mil');

      await expect(getIdToken()).resolves.toBeNull();
      // The SDK was never touched on this path.
      expect(mocks.createdUsers).toHaveLength(0);
      expect(mocks.poolCtorArgs).toHaveLength(0);
    });

    it('logout clears the auto-signed-in email', async () => {
      setPoolConfigured(false);
      const { AuthProvider, useAuth } = await freshAuthContext();
      const { result } = renderHook(() => useAuth(), { wrapper: AuthProvider });
      await waitFor(() => expect(result.current.email).toBe('dev@local'));

      act(() => {
        result.current.logout();
      });

      expect(result.current.email).toBeNull();
    });
  });
});
