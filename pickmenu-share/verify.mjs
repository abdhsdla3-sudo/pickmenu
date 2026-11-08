import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { gzipSync } from "node:zlib";
import worker from "./dist/server/index.js";

const local = process.env.PICKMENU_VERIFY_ORIGIN || "http://127.0.0.1:8765";
const call = (path, init, env) => worker.fetch(new Request("https://preview.invalid" + path, init), env);
const normalize = data => JSON.parse(JSON.stringify(data, (key, value) =>
  ["source_file", "source_row"].includes(key) ? undefined : value));
const cases = [
  {}, { goal: "protein" }, { goal: "light" }, { diet: "vegan", goal: "protein" },
  { diet: "vegetarian", district: "팔달구" }, { exclude: "돼지고기" }, { allergy: "땅콩" },
  { district: "영통구", query: "후라이드 반반" }, { district: "영통구", query: "비빔밥" },
  { diet: "meat", soup: "yes", spice: "hot", category: "치킨" }, { query: "후보존재하지않는입력" },
  { district: "서울" }, { allergy: ",,," }, { query: "x".repeat(41) }, { goal: null }, [],
];
for (const data of cases) {
  const init = {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data),
  };
  const [localResponse, sharedResponse] = await Promise.all([
    fetch(local + "/api/recommend", init), call("/api/recommend", init),
  ]);
  assert.equal(sharedResponse.status, localResponse.status);
  assert.deepEqual(await sharedResponse.json(), normalize(await localResponse.json()), JSON.stringify(data));
}

const catalog = await call("/api/catalog");
const localCatalog = await fetch(local + "/api/catalog");
assert.deepEqual(await catalog.json(), await localCatalog.json());
const menu = await call("/api/menus/MN_effea4fb1e9d3908");
const localMenu = await fetch(local + "/api/menus/MN_effea4fb1e9d3908");
assert.deepEqual(await menu.json(), normalize(await localMenu.json()));
assert.equal((await call("/")).status, 200);
for (const file of ["/catalog.json", "/local/catalog.sqlite3", "/local/drive/", "/server.py", "/worker.js"]) {
  assert.equal((await call(file)).status, 404);
}

const env = { PICKMENU_MENU_API_ORIGIN: "https://api.example.test", PICKMENU_API_TOKEN: "test-only" };
const originalFetch = globalThis.fetch;
try {
  let sent = 0;
  globalThis.fetch = async (url, outgoing) => {
    sent += 1;
    assert.equal(url.href, "https://api.example.test/api/recommend");
    assert.equal(outgoing.headers.Authorization, "Bearer test-only");
    assert.equal(outgoing.headers.Cookie, undefined);
    assert.equal(outgoing.redirect, "manual");
    assert.deepEqual(JSON.parse(outgoing.body), { query: "비빔밥" });
    return Response.json({ items: [] });
  };
  const init = { method: "POST", headers: { "Content-Type": "application/json", Cookie: "browser=only" },
    body: JSON.stringify({ query: "비빔밥" }) };
  assert.deepEqual(await (await call("/api/recommend", init, env)).json(), { items: [] });
  assert.equal((await call("/api/recommend", init, { ...env, PICKMENU_API_TOKEN: "" })).status, 503);
  assert.equal((await call("/api/recommend", init, { ...env, PICKMENU_MENU_API_ORIGIN: "http://api.example.test" })).status, 503);
  assert.equal((await call("/api/recommend", { ...init, body: JSON.stringify("a".repeat(4096)) }, env)).status, 400);
  assert.equal(sent, 1);
  globalThis.fetch = async () => Response.redirect("https://redirect.example.test");
  assert.equal((await call("/api/recommend", init, env)).status, 503);
} finally {
  globalThis.fetch = originalFetch;
}

const loaded = JSON.parse(await readFile("catalog.json", "utf8"));
assert.equal(loaded.filter(item => /[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}|(?:010|011)[- .]?\d{4}[- .]?\d{4}/i
  .test([item.name, ...item.components].join(" "))).length, 0);
const bytes = await readFile("dist/server/index.js");
console.log(`동작 비교 ${cases.length}건 통과, 메뉴 ${loaded.length}개, 압축 크기 ${gzipSync(bytes).length}바이트`);
