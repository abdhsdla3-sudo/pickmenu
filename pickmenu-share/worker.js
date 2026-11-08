/* global MENUS, ASSETS */

const goals = { balance: "균형 식사", protein: "단백질 중심", light: "가벼운 식사" };
const categories = [...new Set(MENUS.map(menu => menu.category))].sort();
const byId = new Map(MENUS.map(menu => [menu.standard_menu_id, menu]));
const options = {
  diet: ["general", "vegetarian", "vegan", "meat"], goal: Object.keys(goals),
  spice: ["", "mild", "hot"], soup: ["", "yes", "no"], category: ["", ...categories],
  district: ["", "권선구", "영통구", "장안구", "팔달구"],
};
const meatPattern = /소고기|쇠고기|우삼겹|돼지|삼겹|닭|오리|족발|보쌈|스테이크|돈까스|돈가스|치킨|베이컨|소시지|곱창|막창|갈비|육회|불고기/;
const seafoodPattern = /생선|새우|해물|해산물|오징어|아귀|아구|참치|연어|멸치|젓갈|굴소스|조개|바지락|전복|장어/;
const veganExclude = /달걀|계란|우유|치즈|버터|유청|요거트|꿀/;
const aliases = [
  ["계란", "달걀", "난류", "에그"], ["우유", "유제품", "치즈", "버터", "유청"],
  ["밀", "밀가루", "밀글루텐"], ["땅콩", "피넛"], ["돼지", "돼지고기", "돈육"],
  ["새우", "쉬림프"], ["대두", "콩", "두부", "두유"],
];
const formatter = new Intl.NumberFormat("ko-KR");
const clip = value => Math.min(1, Math.max(0, value));
const compact = value => value.normalize("NFKC").replace(/\s+/gu, "").toLowerCase().replaceAll("ß", "ss");
const compareId = (a, b) => a.standard_menu_id < b.standard_menu_id ? -1 :
  a.standard_menu_id > b.standard_menu_id ? 1 : 0;

function parseProfile(data) {
  if (!data || typeof data !== "object" || Array.isArray(data)) {
    throw new Error("입력 형식을 확인해 주세요.");
  }
  const profile = {};
  const defaults = { diet: "general", goal: "balance" };
  for (const [field, values] of Object.entries(options)) {
    const value = Object.hasOwn(data, field) ? data[field] : defaults[field] ?? "";
    if (!values.includes(value)) throw new Error("목록에 있는 조건을 선택해 주세요.");
    profile[field] = value;
  }
  for (const [field, limit] of Object.entries({ query: 40, exclude: 100, allergy: 100 })) {
    const value = Object.hasOwn(data, field) ? data[field] : "";
    if (typeof value !== "string" || [...value].length > limit || /[\x00-\x1f]/u.test(value)) {
      throw new Error(`입력은 ${limit}자 이내로 작성해 주세요.`);
    }
    if (field !== "query" && value.trim() && !/[^,;/\s]/u.test(value)) {
      throw new Error("제한 재료 이름을 입력해 주세요.");
    }
    profile[field] = value.trim();
  }
  return profile;
}

function isMenuRequest(method, path) {
  return (method === "POST" && path === "/api/recommend") ||
    (method === "GET" && (path === "/api/catalog" || /^\/api\/menus\/MN_[0-9a-f]{16}$/.test(path)));
}

function ingredientTerms(value) {
  const terms = new Set(value.split(/[,;/]/u).map(compact).filter(Boolean));
  for (const group of aliases) {
    if (group.some(term => terms.has(term))) {
      for (const term of group) terms.add(term);
    }
  }
  return terms;
}

function nutritionScore(nutrition, goal) {
  if (!nutrition || nutrition.basis !== 100 || nutrition.unit !== "g") return null;
  const [kcal, protein, carbs, fat] = nutrition.values;
  if (goal === "protein") return protein === null ? null : clip(protein / 12);
  if (goal === "light") return kcal === null ? null : clip(1 - (kcal - 80) / 220);
  if (!kcal || [protein, carbs, fat].includes(null)) return null;
  const total = 4 * protein + 4 * carbs + 9 * fat;
  if (total <= 0) return null;
  const distance = Math.abs(4 * protein / total - .22) +
    Math.abs(4 * carbs / total - .53) + Math.abs(9 * fat / total - .25);
  return clip(1 - distance);
}

function publicMenu({ regions, ...menu }) {
  return menu;
}

function nearestEven(value) {
  const floor = Math.floor(value);
  return value - floor === 0.5 ? floor + floor % 2 : Math.round(value);
}

function recommend(profile) {
  const ranked = [];
  const check = [];
  let excluded = 0;
  const terms = [...new Set([...ingredientTerms(profile.allergy), ...ingredientTerms(profile.exclude)])];
  const plant = ["vegan", "vegetarian"].includes(profile.diet);
  const region = profile.district || "수원시";
  const query = compact(profile.query);
  for (const menu of MENUS) {
    const evidence = menu.regions[region];
    const signal = [menu.name, menu.category, ...menu.components].join(" ");
    const normalized = compact(signal);
    if (!evidence || !normalized.includes(query)) continue;
    const meat = meatPattern.test(signal);
    const conflict = terms.some(term => normalized.includes(term)) ||
      (plant && (meat || seafoodPattern.test(signal) ||
        (profile.diet === "vegan" && veganExclude.test(signal))));
    if (conflict) {
      excluded += 1;
      continue;
    }
    if (plant || terms.length) {
      check.push({ standard_menu_id: menu.standard_menu_id, name: menu.name, review_count: evidence.reviews });
      continue;
    }
    const reasons = [];
    const preferences = [];
    for (const [label, value, actual] of [
      ["음식군", profile.category, menu.category], ["맵기", profile.spice, menu.spice],
      ["국물", profile.soup, menu.soup],
      ["육식 선호", profile.diet === "meat" ? "yes" : "", meat ? "yes" : ""],
    ]) {
      if (!value) continue;
      preferences.push(value === actual ? 1 : actual ? 0 : 0.35);
      reasons.push(`${label} ${value === actual ? "일치" : actual ? "불일치" : "정보 부족"}`);
    }
    const n = nutritionScore(menu.nutrition, profile.goal);
    const t = preferences.length ? preferences.reduce((sum, value) => sum + value, 0) / preferences.length : 0.5;
    const e = 0.7 * clip(Math.log1p(evidence.reviews) / Math.log1p(150)) +
      0.3 * clip(Math.log1p(evidence.stores) / Math.log1p(10));
    const score = Number(((0.50 * (n ?? 0.30) + (0.35 * t + 0.15 * e)) * 100).toFixed(3));
    reasons.unshift(
      `${region} 리뷰 ${formatter.format(evidence.reviews)}건 · 매장 ${formatter.format(evidence.stores)}곳`,
      n === null ? "비교할 참고 영양값 부족" :
        `${goals[profile.goal]} 적합도 ${nearestEven(n * 100)}점 (일반음식 참고값)`,
    );
    ranked.push({ ...publicMenu(menu), region, evidence, reasons, score });
  }
  ranked.sort((a, b) => b.score - a.score || b.evidence.reviews - a.evidence.reviews || compareId(a, b));
  check.sort((a, b) => b.review_count - a.review_count || compareId(a, b));
  const first = [];
  const alternatives = [];
  const seen = new Set();
  for (const menu of ranked) {
    const family = compact(menu.components[0]);
    (seen.has(family) ? alternatives : first).push(menu);
    seen.add(family);
  }
  return { items: [...first, ...alternatives].slice(0, 5), eligible: ranked.length,
    excluded, unverified: check.length, check_only: check.slice(0, 3), region };
}

const headers = {
  "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
  "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'",
  "X-Robots-Tag": "noindex, nofollow",
};
const json = (value, status = 200) => new Response(JSON.stringify(value), {
  status, headers: { ...headers, "Content-Type": "application/json; charset=utf-8" },
});

async function requestData(request) {
  const reader = request.body?.getReader();
  if (!reader) throw new Error();
  const decoder = new TextDecoder("utf-8", { fatal: true });
  let size = 0;
  let body = "";
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > 4096) {
        await reader.cancel();
        throw new Error();
      }
      body += decoder.decode(value, { stream: true });
    }
    return JSON.parse(body + decoder.decode());
  } finally {
    reader.releaseLock();
  }
}

async function fetchMenuApi(request, path, env) {
  let origin;
  try {
    origin = new URL(env.PICKMENU_MENU_API_ORIGIN);
    if (origin.protocol !== "https:" || origin.username || origin.password ||
      origin.pathname !== "/" || origin.search || origin.hash || !env.PICKMENU_API_TOKEN) {
      throw new Error();
    }
  } catch {
    return json({ error: "메뉴 서버 연결 설정을 확인해 주세요." }, 503);
  }
  const outgoing = {
    method: request.method,
    headers: {
      Accept: "application/json",
      Authorization: `Bearer ${env.PICKMENU_API_TOKEN}`,
    },
    redirect: "manual",
    signal: AbortSignal.timeout(8000),
  };
  if (request.method === "POST") {
    if (request.headers.get("Content-Type")?.split(";")[0].trim() !== "application/json") {
      return json({ error: "입력 형식을 확인해 주세요." }, 415);
    }
    try {
      outgoing.body = JSON.stringify(await requestData(request));
      outgoing.headers["Content-Type"] = "application/json";
    } catch {
      return json({ error: "입력 형식 또는 길이를 확인해 주세요." }, 400);
    }
  }
  try {
    const upstream = await fetch(new URL(path, origin), outgoing);
    if ([200, 400, 404, 415].includes(upstream.status) &&
      upstream.headers.get("Content-Type")?.includes("application/json")) {
      return json(await upstream.json(), upstream.status);
    }
  } catch {
    // 연결 실패나 시간 초과 시 브라우저에 동일한 오류 형식을 반환한다.
  }
  return json({ error: "메뉴 서버에 연결할 수 없어요. 잠시 뒤 다시 시도해 주세요." }, 503);
}

export default {
  async fetch(request, env = {}) {
    const path = new URL(request.url).pathname;
    if (env.PICKMENU_MENU_API_ORIGIN && isMenuRequest(request.method, path)) {
      return fetchMenuApi(request, path, env);
    }
    if (request.method === "GET" || request.method === "HEAD") {
      if (Object.hasOwn(ASSETS, path)) {
        const [type, content] = ASSETS[path];
        return new Response(request.method === "HEAD" ? null : content,
          { headers: { ...headers, "Content-Type": type } });
      }
      if (path === "/api/catalog") {
        return json({ categories, menu_count: MENUS.length, period: "2026년 7~9월" });
      }
      if (path.startsWith("/api/menus/")) {
        const item = byId.get(path.slice("/api/menus/".length));
        return item ? json(publicMenu(item)) : json({ error: "메뉴를 찾을 수 없어요." }, 404);
      }
    }
    if (request.method === "POST" && path === "/api/recommend") {
      if (request.headers.get("Content-Type")?.split(";")[0].trim() !== "application/json") {
        return json({ error: "입력 형식을 확인해 주세요." }, 415);
      }
      let data;
      try {
        data = await requestData(request);
      } catch {
        return json({ error: "입력 형식 또는 길이를 확인해 주세요." }, 400);
      }
      try {
        return json(recommend(parseProfile(data)));
      } catch (error) {
        return json({ error: error.message }, 400);
      }
    }
    return json({ error: "페이지를 찾을 수 없어요." }, 404);
  },
};
