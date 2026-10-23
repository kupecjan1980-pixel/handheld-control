// Builds the plugin's panel (../src/index.tsx) for a normal browser: Decky's
// modules are swapped for the stand-ins in shims/, React is bundled.
import commonjs from "@rollup/plugin-commonjs";
import resolve from "@rollup/plugin-node-resolve";
import replace from "@rollup/plugin-replace";
import typescript from "@rollup/plugin-typescript";
import { copyFileSync, mkdirSync } from "node:fs";
import { fileURLToPath } from "node:url";

const here = (p) => fileURLToPath(new URL(p, import.meta.url));
const SHIMS = { "@decky/ui": here("./shims/decky-ui.tsx"), "@decky/api": here("./shims/decky-api.ts") };

export default {
  input: here("./entry.tsx"),
  output: { file: here("./dist/bundle.js"), format: "iife", sourcemap: false },
  plugins: [
    { name: "decky-shims", resolveId: (id) => SHIMS[id] ?? null },
    typescript({ tsconfig: here("./tsconfig.json") }),
    resolve({ browser: true }),
    commonjs(),
    replace({ preventAssignment: true, values: { "process.env.NODE_ENV": JSON.stringify("production") } }),
    {
      name: "copy-html",
      writeBundle: () => {
        mkdirSync(here("./dist"), { recursive: true });
        copyFileSync(here("./index.html"), here("./dist/index.html"));
      },
    },
  ],
};
