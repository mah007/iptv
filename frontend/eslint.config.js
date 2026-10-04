import js from "@eslint/js";
import { defineConfig, globalIgnores } from "eslint/config";
import i18next from "eslint-plugin-i18next";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";
import tseslint from "typescript-eslint";

// Physical left/right utilities break the Arabic RTL layout. Use the logical
// ones instead: ms/me, ps/pe, start/end, border-s/e, rounded-s/e, text-start/end.
const PHYSICAL_CLASS =
  "/(^|\\s)(-?m[lr]|p[lr]|left|right|border-[lr]|rounded-[lr]|rounded-[tb][lr]|scroll-[mp][lr])-|(^|\\s)(text|float|clear)-(left|right)(\\s|$)/";
const RTL_MESSAGE =
  "Use logical utilities (ms/me, ps/pe, start/end, text-start/end) so the layout mirrors in Arabic.";
// className covers cn(...) calls inside it; cva(...) holds component variants.
const classContexts = ["JSXAttribute[name.name='className']", "CallExpression[callee.name='cva']"];

export default defineConfig([
  // Orval output (packages/api*/src/generated) is regenerated, never hand-edited.
  globalIgnores([
    "**/dist",
    "**/coverage",
    "**/node_modules",
    "packages/api/src/generated/**",
    "packages/api-portal/src/generated/**",
  ]),
  {
    files: ["**/*.{ts,tsx}"],
    extends: [
      js.configs.recommended,
      tseslint.configs.strictTypeChecked,
      tseslint.configs.stylisticTypeChecked,
      reactHooks.configs.flat.recommended,
    ],
    languageOptions: {
      globals: globals.browser,
      parserOptions: {
        projectService: {
          // The Orval configs are type-checked under Node (tsconfig.node.json), outside the
          // packages' browser tsconfig, so the DOM's File isn't shadowed in the clients.
          allowDefaultProject: ["packages/*/orval.config.ts"],
        },
        tsconfigRootDir: import.meta.dirname,
      },
    },
    rules: {
      "no-restricted-syntax": [
        "error",
        ...classContexts.flatMap((context) => [
          { selector: `${context} Literal[value=${PHYSICAL_CLASS}]`, message: RTL_MESSAGE },
          {
            selector: `${context} TemplateElement[value.raw=${PHYSICAL_CLASS}]`,
            message: RTL_MESSAGE,
          },
        ]),
      ],
    },
  },
  {
    // Every user-facing string goes through i18next (ar + en).
    files: ["**/src/**/*.tsx"],
    ignores: ["**/*.test.tsx"],
    extends: [i18next.configs["flat/recommended"]],
  },
  {
    files: ["**/*.js"],
    extends: [js.configs.recommended],
    languageOptions: { globals: globals.node },
  },
]);
