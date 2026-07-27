/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string;
  readonly VITE_BASE_PATH?: string;
  readonly VITE_COGNITO_USER_POOL_ID?: string;
  readonly VITE_COGNITO_CLIENT_ID?: string;
  readonly VITE_AWS_REGION?: string;
  readonly VITE_COGNITO_ENDPOINT?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
