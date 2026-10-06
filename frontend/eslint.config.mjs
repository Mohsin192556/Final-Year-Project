import tseslint from "typescript-eslint";

const eslintConfig = [
  {
    ignores: [".next/**", "out/**", "build/**", "next-env.d.ts"],
  },
  ...tseslint.configs.recommended,
];

export default eslintConfig;
