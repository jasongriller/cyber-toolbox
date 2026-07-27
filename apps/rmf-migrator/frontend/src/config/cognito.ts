/** Cognito wiring, baked at build time by deploy.sh. All empty in dev/tests,
 *  which switches AuthContext into NO_AUTH mode (AuthContext.isNoAuthMode) —
 *  AuthGate then renders children directly, with no login form and no
 *  signed-in header. */
export const cognitoConfig = {
  userPoolId: import.meta.env.VITE_COGNITO_USER_POOL_ID ?? '',
  clientId: import.meta.env.VITE_COGNITO_CLIENT_ID ?? '',
  region: import.meta.env.VITE_AWS_REGION ?? '',
  /** FIPS endpoint override — REQUIRED in GovCloud (the SDK default hostname
   *  does not resolve there). Empty ⇒ SDK default (commercial regions). */
  endpoint: import.meta.env.VITE_COGNITO_ENDPOINT ?? '',
};
