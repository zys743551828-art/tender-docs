import { cpSync, existsSync, mkdirSync } from "node:fs";
import { join } from "node:path";

for (const directory of ["cmaps", "standard_fonts", "wasm", "iccs"]) {
  const source = join("node_modules/pdfjs-dist", directory);
  const target = join("public/pdfjs", directory);
  if (existsSync(source)) {
    mkdirSync(target, { recursive: true });
    cpSync(source, target, { recursive: true });
  }
}

cpSync("node_modules/pdfjs-dist/LICENSE", "public/pdfjs/LICENSE");
