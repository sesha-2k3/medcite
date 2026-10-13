import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import globals from "globals";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist", "src/api/schema.d.ts"] },
  {
    extends: [js.configs.recommended, ...tseslint.configs.strict],
    files: ["**/*.{ts,tsx}"],
    languageOptions: { ecmaVersion: 2022, globals: globals.browser },
    plugins: { "react-hooks": reactHooks, "react-refresh": reactRefresh },
    rules: {
      ...reactHooks.configs.recommended.rules,
      "react-refresh/only-export-components": ["warn", { allowConstantExport: true }],
      // Components must go through src/api/, never fetch directly (handoff §9).
      "no-restricted-globals": [
        "error",
        { name: "fetch", message: "Use the client in src/api/ instead of calling fetch." },
      ],
    },
  },
  {
    files: ["src/api/**/*.{ts,tsx}"],
    rules: { "no-restricted-globals": "off" },
  },
  {
    files: ["src/components/ui/**/*.tsx"],
    rules: { "react-refresh/only-export-components": "off" },
  },
);
