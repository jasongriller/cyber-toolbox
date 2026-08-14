import js from "@eslint/js";
import globals from "globals";
import reactHooks from "eslint-plugin-react-hooks";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist"] },
  {
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    files: ["**/*.{ts,tsx}"],
    languageOptions: {
      ecmaVersion: 2022,
      globals: globals.browser,
    },
    plugins: {
      "react-hooks": reactHooks,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      // New in eslint-plugin-react-hooks 7 (pulled in by the eslint 10
      // security upgrade). It flags the fetch-on-mount pattern used across
      // this app; the async loads set state after awaits, not synchronously,
      // so the cascading-render concern doesn't apply. Revisit if these
      // components are ever reworked.
      "react-hooks/set-state-in-effect": "off",
    },
  },
);
