import { copyFile, mkdir, readFile, writeFile } from "node:fs/promises";

const read = path => readFile(new URL(path, import.meta.url), "utf8");
const urls = {
  "/": ["index.html", "text/html; charset=utf-8"],
  "/app.js": ["app.js", "text/javascript; charset=utf-8"],
  "/style.css": ["style.css", "text/css; charset=utf-8"],
  "/mark.svg": ["mark.svg", "image/svg+xml"],
};
const assets = {};
for (const [url, [name, type]] of Object.entries(urls)) {
  assets[url] = [type, await read(`web/${name}`)];
}
assets["/menu-photos.webp"] = ["image/webp", await readFile(
  new URL("web/menu-photos.webp", import.meta.url), "base64"), true];
const catalog = JSON.parse(await read("catalog.json"));
const worker = await read("worker.js");
await mkdir("dist/server", { recursive: true });
if (!process.argv.includes("--local")) {
  await mkdir("dist/.openai", { recursive: true });
  await copyFile(".openai/hosting.json", "dist/.openai/hosting.json");
}
await writeFile("dist/server/index.js",
  `const MENUS = ${JSON.stringify(catalog)};\nconst ASSETS = ${JSON.stringify(assets)};\n${worker}`,
  "utf8",
);
