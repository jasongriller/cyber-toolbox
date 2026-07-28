import { createContext, useContext, useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import {
  CognitoUserPool,
  CognitoUser,
  AuthenticationDetails,
} from 'amazon-cognito-identity-js';
import type { CognitoUserSession } from 'amazon-cognito-identity-js';
import { cognitoConfig } from '../config/cognito';

const userPool = cognitoConfig.userPoolId
  ? new CognitoUserPool({
      UserPoolId: cognitoConfig.userPoolId,
      ClientId: cognitoConfig.clientId,
      ...(cognitoConfig.endpoint ? { endpoint: cognitoConfig.endpoint } : {}),
    })
  : null;

// Nothing to log into: a fresh clone or a test run with empty VITE_COGNITO_*.
// Auto-"sign in" as dev@local and skip the login gate entirely rather than
// hanging on a screen with no backend to authenticate against. Deployed builds
// set real VITE_COGNITO_* (deploy.sh), so userPool is non-null and real
// Cognito auth applies.
const NO_AUTH = !userPool;

type MfaStage = 'none' | 'setup' | 'code';

interface AuthContextValue {
  email: string | null;
  loading: boolean;
  /** True when Cognito returned newPasswordRequired — Login switches to the set-password form. */
  requiresNewPassword: boolean;
  /** 'setup': Cognito wants a new TOTP device enrolled (mfaSecret is populated).
   *  'code': Cognito wants a code from an already-enrolled device. Login
   *  switches views off this. */
  mfaStage: MfaStage;
  /** The freshly-issued TOTP secret while mfaStage === 'setup'; null otherwise. */
  mfaSecret: string | null;
  login: (email: string, password: string) => Promise<void>;
  /** Completes the newPasswordRequired challenge. Call after login() sets requiresNewPassword. */
  completeNewPassword: (newPassword: string) => Promise<void>;
  /** Completes a TOTP challenge — enrollment verification (mfaStage 'setup') or an
   *  ordinary SOFTWARE_TOKEN_MFA code (mfaStage 'code'). */
  submitTotpCode: (code: string) => Promise<void>;
  /** Abandons an in-flight challenge (new password / MFA) so Login can return
   *  to the sign-in form — e.g. a mistyped email discovered at the code prompt. */
  resetChallenge: () => void;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [email, setEmail] = useState<string | null>(NO_AUTH ? 'dev@local' : null);
  const [loading, setLoading] = useState(!NO_AUTH);
  // Holds the CognitoUser across a challenge (newPasswordRequired / MFA setup / MFA
  // code) — admin-created first login can chain all three. Never exposed directly;
  // Login interacts through requiresNewPassword / mfaStage / completeNewPassword /
  // submitTotpCode.
  const [pendingUser, setPendingUser] = useState<CognitoUser | null>(null);
  const [mfaStage, setMfaStage] = useState<MfaStage>('none');
  const [mfaSecret, setMfaSecret] = useState<string | null>(null);

  useEffect(() => {
    if (NO_AUTH) return;
    const current = userPool!.getCurrentUser();
    if (current) {
      current.getSession((err: Error | null, session: CognitoUserSession | null) => {
        if (!err && session && session.isValid()) setEmail(current.getUsername());
        setLoading(false);
      });
    } else {
      setLoading(false);
    }
  }, []);

  // mfaSetup/totpRequired fire from both authenticateUser and
  // completeNewPasswordChallenge — an admin-created account chains
  // NEW_PASSWORD_REQUIRED straight into MFA_SETUP on its first login. Both
  // challenges hand the callback object the same CognitoUser and the same
  // reject, so one factory covers both call sites. It also carries the
  // challenge types this app does not implement (SMS/select-MFA/custom): the
  // pool only issues TOTP challenges today, but if that ever changed, leaving
  // these three off the callback object would mean the SDK calls a callback
  // that was never supplied — login() would never resolve or reject, and the
  // caller would hang forever instead of failing loudly.
  function challengeCallbacks(user: CognitoUser, reject: (err: Error) => void) {
    return {
      mfaSetup: () => {
        setPendingUser(user);
        user.associateSoftwareToken({
          associateSecretCode: (secret: string) => {
            setMfaSecret(secret);
            setMfaStage('setup');
            reject(new Error('mfa-setup-required'));
          },
          onFailure: (err: Error) => reject(err),
        });
      },
      totpRequired: () => {
        setPendingUser(user);
        setMfaStage('code');
        reject(new Error('totp-required'));
      },
      mfaRequired: () => reject(new Error('unsupported challenge: SMS_MFA')),
      selectMFAType: () => reject(new Error('unsupported challenge: SELECT_MFA_TYPE')),
      customChallenge: () => reject(new Error('unsupported challenge: CUSTOM_CHALLENGE')),
    };
  }

  const login = (emailAddr: string, password: string) =>
    new Promise<void>((resolve, reject) => {
      if (NO_AUTH) {
        setEmail(emailAddr || 'dev@local');
        return resolve();
      }
      const user = new CognitoUser({ Username: emailAddr, Pool: userPool! });
      const details = new AuthenticationDetails({ Username: emailAddr, Password: password });
      user.authenticateUser(details, {
        onSuccess: () => {
          setEmail(emailAddr);
          resolve();
        },
        onFailure: (err) => reject(err),
        newPasswordRequired: () => {
          // Store the user so completeNewPassword calls the challenge on the same instance.
          setPendingUser(user);
          reject(new Error('new-password-required'));
        },
        ...challengeCallbacks(user, reject),
      });
    });

  const completeNewPassword = (newPassword: string) =>
    new Promise<void>((resolve, reject) => {
      if (NO_AUTH) {
        setEmail('dev@local');
        return resolve();
      }
      if (!pendingUser) {
        reject(new Error('No pending challenge'));
        return;
      }
      pendingUser.completeNewPasswordChallenge(newPassword, {}, {
        onSuccess: () => {
          setEmail(pendingUser.getUsername());
          setPendingUser(null);
          resolve();
        },
        onFailure: (err) => {
          reject(err);
        },
        ...challengeCallbacks(pendingUser, reject),
      });
    });

  const submitTotpCode = (code: string) =>
    new Promise<void>((resolve, reject) => {
      if (!pendingUser) return reject(new Error('No pending challenge'));
      const done = {
        onSuccess: () => {
          setEmail(pendingUser.getUsername());
          setPendingUser(null);
          setMfaStage('none');
          setMfaSecret(null);
          resolve();
        },
        onFailure: (err: Error) => reject(err),
      };
      if (mfaStage === 'setup') {
        pendingUser.verifySoftwareToken(code, 'cyber-toolbox', done);
      } else {
        pendingUser.sendMFACode(code, done, 'SOFTWARE_TOKEN_MFA');
      }
    });

  // Dropping the pending CognitoUser means the only way forward is a fresh
  // login() — exactly the semantics "Back to sign in" needs. Deliberately does
  // not touch email: an already-signed-in session is not a challenge.
  const resetChallenge = () => {
    setPendingUser(null);
    setMfaStage('none');
    setMfaSecret(null);
  };

  const logout = () => {
    if (userPool) {
      const current = userPool.getCurrentUser();
      if (current) current.signOut();
    }
    setEmail(null);
    setPendingUser(null);
    setMfaStage('none');
    setMfaSecret(null);
  };

  return (
    <AuthContext.Provider
      value={{
        email,
        loading,
        // Gated on mfaStage: pendingUser is also set mid MFA_SETUP/SOFTWARE_TOKEN_MFA
        // challenges, and those must not be mistaken for the new-password view.
        requiresNewPassword: pendingUser !== null && mfaStage === 'none',
        mfaStage,
        mfaSecret,
        login,
        completeNewPassword,
        submitTotpCode,
        resetChallenge,
        logout,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}

/** True when no Cognito pool is configured (every local/test run today).
 *  Module-scope, not context: AuthGate needs it to decide whether to render a
 *  signed-in header at all — a dev clicking "Sign out" in NO_AUTH mode would
 *  land on a login form with no backend to authenticate against, so NO_AUTH
 *  renders bare children instead of offering a control with nothing behind it. */
export const isNoAuthMode = NO_AUTH;

/** Current user's Cognito ID token (JWT) for the Authorization header, or null if
 *  not signed in. Module-scope, not context: api.ts needs it without rendering
 *  inside AuthProvider, and the session auto-refreshes via the 30-day refresh token. */
export function getIdToken(): Promise<string | null> {
  return new Promise((resolve) => {
    if (NO_AUTH) return resolve(null);
    const current = userPool!.getCurrentUser();
    if (!current) return resolve(null);
    current.getSession((err: Error | null, session: CognitoUserSession | null) => {
      if (err || !session || !session.isValid()) return resolve(null);
      resolve(session.getIdToken().getJwtToken());
    });
  });
}
