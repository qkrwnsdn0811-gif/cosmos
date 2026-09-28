import { build } from "esbuild";
import { fileURLToPath } from "node:url";

for (const [entryPoint, outfile] of [
  ["services/news_pipeline/domestic_discover.mjs", "services/news_pipeline/domestic.bundle.mjs"],
  ["services/news_pipeline/naver_fetch.mjs", "services/news_pipeline/naver-fetch.bundle.mjs"],
]) await build({
  absWorkingDir: fileURLToPath(new URL("..", import.meta.url)),
  entryPoints: [entryPoint],
  outfile,
  bundle: true, platform: "node", format: "esm", target: "node22",
  banner: { js: 'import { createRequire as __createRequire } from "node:module"; const require = __createRequire(import.meta.url);' },
});
