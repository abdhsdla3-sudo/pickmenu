import { copyFile, mkdir, readdir, readFile, writeFile } from "node:fs/promises";

const read = path => readFile(new URL(path, import.meta.url), "utf8");
const urls = {
  "/": ["index.html", "text/html; charset=utf-8"],
  "/app.js": ["app.js", "text/javascript; charset=utf-8"],
  "/style.css": ["style.css", "text/css; charset=utf-8"],
  "/mark.svg": ["mark.svg", "image/svg+xml"],
  "/meal-category-icons.webp": ["meal-category-icons.webp", "image/webp", true],
};
const assets = {};
for (const [url, [name, type, binary]] of Object.entries(urls)) {
  assets[url] = binary
    ? [type, await readFile(new URL(`web/${name}`, import.meta.url), "base64"), true]
    : [type, await read(`web/${name}`)];
}
const photos = (await readdir(new URL("web/menu-photos/", import.meta.url)))
  .filter(name => /^\d{3}\.webp$/.test(name))
  .sort();
for (const name of photos) {
  assets[`/menu-photos/${name}`] = ["image/webp", await readFile(
    new URL(`web/menu-photos/${name}`, import.meta.url), "base64"), true];
}
const catalog = JSON.parse(await read("catalog.json"));
const worker = await read("worker.js");
const reviewPeriod = process.env.PICKMENU_REVIEW_PERIOD || (await read("../pickmenu/review_period.txt")).trim();
await mkdir("dist/server", { recursive: true });
if (!process.argv.includes("--local")) {
  await mkdir("dist/.openai", { recursive: true });
  await copyFile(".openai/hosting.json", "dist/.openai/hosting.json");
}
await writeFile("dist/server/index.js",
  `const REVIEW_PERIOD = ${JSON.stringify(reviewPeriod)};\nconst MENUS = ${JSON.stringify(catalog)};\nconst ASSETS = ${JSON.stringify(assets)};\n${worker}`,
  "utf8",
);
