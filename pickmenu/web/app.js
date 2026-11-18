"use strict";

const form = document.querySelector("#profile");
const status = document.querySelector("#result-status");
const profileStatus = document.querySelector("#profile-status");
const results = document.querySelector("#results");
const subtitle = document.querySelector("#results-sub");
const dialog = document.querySelector("#detail");
const scroller = document.querySelector("#app-scroll");
const chatLog = document.querySelector("#chat-log");
const chatChips = document.querySelector("#chat-chips");
const chatInput = document.querySelector("#chat-input");
const storageKey = "pickmenu.profile.v2";
const legacyKey = "pickmenu.profile.v1";
const preferenceFields = ["diet", "goal", "spice", "soup", "category", "exclude", "allergy"];
const personalFields = ["birth_date", "height", "weight", "gender"];
const goalLabels = {balance: "균형 식사", protein: "단백질 중심", light: "가벼운 식사"};
const dietLabels = {general: "일반식", vegetarian: "채식", vegan: "비건", meat: "육식 선호"};
const labels = ["열량", "단백질", "탄수화물", "지방", "나트륨"];
const units = ["kcal", "g", "g", "g", "mg"];
const defaults = {nickname: "", age: "", birth_date: "", height: "", weight: "", gender: "", diet: "general",
  goal: "balance", spice: "", soup: "", category: "", exclude: "", allergy: ""};
let profile = {...defaults};
let hasProfile = false;
let step = 0;
let sourceMode = "choice";
let chatMessages = [];
let chatConditions = {};
let activeRecommendation = null;
let lastResult = null;
let storageNotice = "";
let categoryTouched = false;
// 기본정보·취향은 서버에 암호화해 저장하며 브라우저 저장소에는 남기지 않는다.
const serverFields = ["nickname", ...personalFields, ...preferenceFields];
const authForm = document.querySelector("#auth-form");
const authStatus = document.querySelector("#auth-status");
let account = null;
let accountReturn = "welcome";
let syncNotice = "";
document.querySelector("#category").addEventListener("change", () => { categoryTouched = true; });

function showScreen(name) {
  if (!account && !["welcome", "account"].includes(name)) {
    openAccount();
    return;
  }
  for (const screen of document.querySelectorAll("[data-screen]")) {
    screen.hidden = screen.dataset.screen !== name;
  }
  document.querySelector(".bottom-nav").hidden = ["welcome", "onboarding"].includes(name)
    || (name === "account" && !hasProfile);
  scroller.scrollTop = 0;
  const title = document.querySelector(`[data-screen="${name}"] h2`);
  if (title) title.focus({preventScroll: true});
}

function cancelRecommendation() {
  activeRecommendation?.abort();
  activeRecommendation = null;
  setBusy(false);
}

function setBusy(busy) {
  document.querySelector("#meal button[type=submit]").disabled = busy;
  document.querySelector("#chat-form button").disabled = busy;
  chatInput.disabled = busy;
  for (const button of chatChips.querySelectorAll("button")) button.disabled = busy;
}

function summary() {
  return [dietLabels[profile.diet], goalLabels[profile.goal], profile.category || "음식군 골고루",
    profile.allergy ? "알레르기 설정" : "", profile.exclude ? "기피 재료 설정" : ""].filter(Boolean).join(" · ");
}

function applyProfile() {
  for (const key of ["nickname", ...personalFields, ...preferenceFields]) {
    const field = form.elements.namedItem(key);
    const value = profile[key];
    if (field instanceof RadioNodeList) {
      if (Array.from(field).some(input => input.value === value)) field.value = value;
    } else if (field instanceof HTMLSelectElement) {
      if (Array.from(field.options).some(option => option.value === value)) field.value = value;
    } else {
      field.value = value;
    }
  }
  updateAgeLabels();
}

function updateAgeLabels() {
  const parts = new Intl.DateTimeFormat("en", {timeZone: "Asia/Seoul", year: "numeric",
    month: "2-digit", day: "2-digit"}).formatToParts(new Date());
  const part = type => parts.find(value => value.type === type).value;
  const today = `${part("year")}-${part("month")}-${part("day")}`;
  for (const [id, label] of [["signup-birth-date", "signup-age"], ["profile-birth-date", "profile-age"]]) {
    const input = document.getElementById(id);
    input.max = today;
    let message = "생년월일을 선택하면 만 나이를 계산해요.";
    if (input.value) {
      const age = Number(today.slice(0, 4)) - Number(input.value.slice(0, 4))
        - Number(today.slice(5) < input.value.slice(5));
      message = input.value <= today && age >= 0 && age <= 120 ? `만 ${age}세` : "생년월일을 확인해 주세요.";
    }
    document.getElementById(label).textContent = message;
  }
}
for (const input of document.querySelectorAll("input[name=birth_date]")) {
  input.addEventListener("input", updateAgeLabels);
}

function updateStep() {
  for (const slide of document.querySelectorAll("[data-step]")) {
    slide.hidden = Number(slide.dataset.step) !== step;
  }
  document.querySelector("#step-label").textContent = `프로필 만들기 · ${step + 1} / 4`;
  document.querySelector("#progress-fill").style.width = `${(step + 1) * 25}%`;
  document.querySelector("#skip-body").hidden = step === 0;
  document.querySelector("#skip-body").textContent = "취향은 나중에";
  document.querySelector("#step-next").replaceChildren(
    document.createTextNode(step === 3 ? "내 프로필 완성하기" : "다음으로"), node("span", "→"));
  profileStatus.textContent = "";
  scroller.scrollTop = 0;
  document.querySelector(`[data-step="${step}"] h2`).focus({preventScroll: true});
}

function startOnboarding() {
  if (!account) return openAccount();
  cancelRecommendation();
  applyProfile();
  step = 0;
  showScreen("onboarding");
  updateStep();
}

function openModes() {
  if (!account) return openAccount();
  updateAccountUI();
  if (!personalFields.every(key => profile[key])) return startOnboarding();
  cancelRecommendation();
  document.querySelector("#greeting-name").textContent = profile.nickname || "메뉴고민러";
  document.querySelector("#profile-summary").textContent = [summary(), storageNotice, syncNotice].filter(Boolean).join(" · ");
  showScreen("modes");
}

function profilePayload() {
  return Object.fromEntries(preferenceFields.map(key => [key, profile[key]]));
}

function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}

function empty(title, body) {
  const box = node("div", undefined, "empty");
  box.append(node("strong", title), node("p", body));
  results.replaceChildren(box);
}

async function request(url, options) {
  let response, data;
  try {
    response = await fetch(url, options);
    data = await response.json();
  } catch {
    throw new Error("추천 서버에 연결할 수 없어요. 잠시 뒤 다시 시도해 주세요.");
  }
  if (!response.ok) {
    if (response.status === 401 && !url.startsWith("/api/auth/")) {
      account = null;
      clearLocalProfile();
      updateAccountUI();
      openAccount();
      authStatus.textContent = "로그인이 만료됐어요. 다시 로그인해 주세요.";
    }
    throw new Error(data.error || "요청을 처리할 수 없어요.");
  }
  return data;
}

function addReasons(parent, reasons) {
  const list = node("ul", undefined, "reason");
  for (const reason of reasons) list.append(node("li", reason));
  parent.append(list);
}

function nutritionSummary(menu) {
  if (!menu.nutrition) {
    return menu.references.length ? "구성 음식의 100g 참고값 · 메뉴 전체값 미확인"
      : `영양정보 없음 · ${menu.nutrition_status}`;
  }
  const ref = menu.nutrition;
  return `${ref.name} 100g 참고 · ${ref.values[0]} kcal · 단백질 ${ref.values[1]} g · ${ref.source} · 자동 연결`;
}

function showReference(parent, ref) {
  parent.append(node("h3", `${ref.name} · ${ref.basis} ${ref.unit}당`));
  const values = node("dl", undefined, "nutrition");
  labels.forEach((label, index) => {
    const value = ref.values[index];
    values.append(node("dt", label), node("dd", value === null ? "정보 없음" : `${value} ${units[index]}`));
  });
  const source = node("a", ref.source);
  source.href = ref.source_url;
  source.target = "_blank";
  source.rel = "noopener noreferrer";
  const provenance = node("p", undefined, "muted");
  provenance.append(source, ` · ${ref.food_id} · 원자료 기준량 ${ref.original_basis}`);
  parent.append(values, provenance, node("p", `${ref.method} · ${ref.confidence}`, "muted"));
  if (ref.serving_reference) {
    parent.append(node("p", `공공 DB 1회분량 참고 표기 ${ref.serving_reference}`, "muted"));
  }
}

function currentDistrict() {
  return sourceMode === "chat" ? document.querySelector("#chat-district").value
    : document.querySelector("#meal").elements.namedItem("district").value;
}

async function loadDetailStores(menu, box) {
  // 기존 메뉴와 가게 자료는 ID 변환표가 없어 구성 음식 이름이 정확히 같을 때만 연결한다.
  const district = currentDistrict();
  const query = new URLSearchParams();
  for (const food of menu.components) query.append("food", food);
  if (district) query.set("district", district);
  const heading = box.firstChild;
  try {
    const data = await request(`/api/v2/stores?${query}`);
    box.replaceChildren(heading);
    if (!data.stores.length) {
      box.append(node("p", "이 메뉴와 연결된 가게 리뷰 자료가 아직 없어요.", "muted"));
      return;
    }
    const where = data.citywide ? `${district}에는 없어 수원 전체 기준` : (district || "수원 전체");
    box.append(node("p", `${data.foods.join("·")} · ${where} · 가게 ${data.store_count}곳`, "muted"));
    const list = node("ul", undefined, "store-list");
    data.stores.forEach((store, at) => {
      const item = storeItem(store);
      item.hidden = at >= 5;
      list.append(item);
    });
    box.append(list);
    if (data.stores.length > 5) {
      const more = node("button", `가게 ${data.stores.length - 5}곳 더 보기`, "more-stores");
      more.type = "button";
      more.addEventListener("click", () => {
        for (const item of list.children) item.hidden = false;
        more.remove();
      });
      box.append(more);
    }
    box.append(node("p", "별점은 리뷰 내용을 분석한 만족도예요 (플랫폼 별점과 달라요). 가게 메뉴 이름이 달라도 같은 음식으로 분류된 메뉴를 보여 줘요.", "muted"));
  } catch (error) {
    box.replaceChildren(heading, node("p", `가게 목록을 불러오지 못했어요. ${error.message}`, "muted"));
  }
}

async function openDetail(item, button) {
  button.disabled = true;
  try {
    const menu = await request(`/api/menus/${encodeURIComponent(item.standard_menu_id)}`);
    document.querySelector("#detail-title").textContent = menu.name;
    const body = document.querySelector("#detail-body");
    body.replaceChildren(node("p", [menu.category, ...menu.components].join(" · "), "muted"));
    addReasons(body, item.reasons);
    body.append(node("p", "실제 매장 레시피와 원재료, 알레르기 및 교차접촉 정보는 확인이 필요해요.", "muted"));
    if (menu.references.length) {
      body.append(node("p", menu.compound
        ? "구성 음식별 참고값입니다. 전체 메뉴나 매장 1인분의 영양량은 확인되지 않았어요."
        : "공개 일반음식 참고값입니다. 실제 매장 제공량과 조리법에 따라 달라요.", "muted"));
      for (const ref of menu.references) showReference(body, ref);
    } else {
      body.append(node("p", `영양정보 없음 · ${menu.nutrition_status}`, "muted"));
    }
    body.append(node("p", `표준 메뉴 ID ${menu.standard_menu_id} · 음식 분류 ${menu.mapping_method || "제공 자료"}`, "muted"));
    const storeBox = node("section", undefined, "detail-stores");
    storeBox.append(node("h3", "이 메뉴를 파는 가게"), node("p", "가게를 찾고 있어요.", "muted"));
    // 가게 목록은 메뉴 정보 바로 아래, 다른 정보보다 먼저 보이게 둔다.
    body.insertBefore(storeBox, body.children[2] || null);
    dialog.showModal();
    loadDetailStores(menu, storeBox);
  } catch (error) {
    status.textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

function render(data) {
  subtitle.textContent = `${data.region} · 2026년 7~9월 배달 리뷰 기준`;
  if (!data.items.length) {
    empty("조건에 맞는 추천 메뉴가 없어요",
      data.unverified ? "원재료·교차접촉 확인이 필요한 메뉴는 추천 목록에서 제외했어요."
        : "오늘의 메뉴 검색어 또는 지역을 바꾸어 다시 찾아보세요.");
    status.textContent = `제한 충돌 ${data.excluded.toLocaleString("ko-KR")}종 · 확인 필요 ${data.unverified.toLocaleString("ko-KR")}종`;
    if (data.check_only.length) {
      const note = node("div", undefined, "card");
      note.append(node("strong", "확인 필요 메뉴 예시"));
      addReasons(note, data.check_only.map(item => item.name));
      results.append(note);
    }
    return;
  }
  lastResult = data;
  if (data.families?.length) {
    renderFamilies(data);
    return;
  }
  status.textContent = data.items.length < 3 ? `현재 조건에서 ${data.items.length}개를 찾았어요. 검색어와 지역은 직접 바꿀 수 있어요.` : "";
  results.replaceChildren();
  data.items.forEach((item, index) => {
    const card = node("article", undefined, "card");
    const head = node("div", undefined, "card-head");
    const title = node("h3");
    title.append(node("span", String(index + 1).padStart(2, "0"), "rank"), item.name);
    head.append(title, node("span", item.category, "badge"));
    const button = node("button", "이 메뉴 선택", "detail-link");
    button.type = "button";
    button.setAttribute("aria-label", `${item.name} 상세 보기`);
    button.addEventListener("click", () => openDetail(item, button));
    card.append(head);
    addReasons(card, item.reasons);
    card.append(node("p", nutritionSummary(item), "muted"),
      node("p", "원재료·알레르기 정보 부족 · 주문 전 확인 필요", "muted"), button);
    results.append(card);
  });
}

// 1단계: 가게마다 다른 메뉴 이름을 표준 음식명(대표 메뉴)으로 묶어 고르게 한다.
function renderFamilies(data) {
  const count = data.families.length;
  status.textContent = count < 3 ? `현재 조건에서 대표 메뉴 ${count}개를 찾았어요. 검색어와 지역은 직접 바꿀 수 있어요.`
    : "먹고 싶은 메뉴를 고르면 실제 메뉴와 파는 가게를 보여 드려요.";
  results.replaceChildren();
  data.families.forEach((family, index) => {
    const card = node("article", undefined, "card");
    const head = node("div", undefined, "card-head");
    const title = node("h3");
    title.append(node("span", String(index + 1).padStart(2, "0"), "rank"), family.name);
    head.append(title, node("span", family.category, "badge"));
    const evidence = family.evidence
      ? `${data.region} 리뷰 ${family.evidence.reviews.toLocaleString("ko-KR")}건 · 가게 ${family.evidence.stores.toLocaleString("ko-KR")}곳`
      : family.best.reasons[0];
    const reasons = [evidence, `가게별 메뉴 ${family.menu_count.toLocaleString("ko-KR")}종`, ...family.best.reasons.slice(1)];
    const button = node("button", "이 메뉴 고르기", "detail-link");
    button.type = "button";
    button.setAttribute("aria-label", `${family.name} 고르고 파는 가게 보기`);
    button.addEventListener("click", () => showFamily(family, data));
    card.append(head);
    addReasons(card, reasons);
    // LLM 한 줄 설명(comment)이 오면 영양·알레르기 문구 대신 보여 준다. 알레르기 확인 안내는 상세에 남는다.
    if (family.comment) card.append(node("p", family.comment, "menu-comment"), button);
    else card.append(node("p", nutritionSummary(family.best), "muted"),
      node("p", "원재료·알레르기 정보 부족 · 주문 전 확인 필요", "muted"), button);
    results.append(card);
  });
  results.append(node("p", "가게마다 다른 메뉴 이름을 같은 음식으로 묶었어요 (자동 분류 · 사람 미검수).", "muted family-note"));
}

function familyStoreItem(store, family) {
  const item = node("li", undefined, "family-store");
  const text = node("div");
  const name = node("p", undefined, "family-store-name");
  name.append(node("strong", store.menu_name), node("span", ` – ${store.store_name || "가게 이름 확인 중"}`));
  text.append(name, node("p", `${store.district} · 이 가게 리뷰 ${store.reviews.toLocaleString("ko-KR")}건`, "store-meta"));
  if (store.comment) text.append(node("p", `AI 설명 · ${store.comment}`, "menu-comment"));
  const button = node("button", "상세", "small-link");
  button.type = "button";
  button.setAttribute("aria-label", `${store.menu_name} 상세 보기`);
  const member = family.members.find(m => m.standard_menu_id === store.standard_menu_id);
  button.addEventListener("click", () => openDetail({standard_menu_id: store.standard_menu_id,
    reasons: member ? member.reasons : []}, button));
  item.append(text, button);
  return item;
}

// 2단계: 고른 대표 메뉴의 실제 메뉴 이름과 판매 가게를 추천한다.
async function showFamily(family, data) {
  const back = node("button", "← 다른 메뉴 고르기", "secondary family-back");
  back.type = "button";
  back.addEventListener("click", () => {
    render(lastResult);
    scroller.scrollTop = 0;
  });
  const heading = node("h3", `${family.name} 파는 가게`, "family-title");
  heading.tabIndex = -1;
  results.replaceChildren(back, heading);
  status.textContent = "가게를 찾고 있어요.";
  scroller.scrollTop = 0;
  heading.focus({preventScroll: true});
  const district = data.region === "수원시" ? "" : data.region;
  try {
    const found = await request("/api/recommend/stores", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({menu_ids: family.members.map(m => m.standard_menu_id), district}),
    });
    if (!results.contains(heading)) return;
    status.textContent = "";
    if (!found.stores.length) {
      results.append(node("p", "이 메뉴를 판 가게 리뷰 자료를 찾지 못했어요.", "muted"));
      return;
    }
    const where = found.citywide ? `${district}에는 없어 수원 전체 기준` : (district || "수원 전체");
    results.append(node("p", `${where} · 가게 ${found.store_count}곳 · 추천 순서는 메뉴 적합도, 그다음 가게 리뷰 수예요.`, "muted"));
    const list = node("ul", undefined, "store-list family-stores");
    found.stores.forEach((store, at) => {
      const item = familyStoreItem(store, family);
      item.hidden = at >= 10;
      list.append(item);
    });
    results.append(list);
    if (found.stores.length > 10) {
      const more = node("button", `${found.stores.length - 10}개 더 보기`, "more-stores");
      more.type = "button";
      more.addEventListener("click", () => {
        for (const item of list.children) item.hidden = false;
        more.remove();
      });
      results.append(more);
    }
    if (!found.names_available) results.append(node("p", "가게 이름 자료를 지금 불러올 수 없어요. 잠시 뒤 다시 시도해 주세요.", "muted"));
    results.append(node("p", "과거 리뷰에 등장한 메뉴와 가게예요. 현재 판매 여부·가격은 주문 전 확인해 주세요.", "muted"));
  } catch (error) {
    if (!results.contains(heading)) return;
    status.textContent = "";
    results.append(node("p", `가게 목록을 불러오지 못했어요. ${error.message}`, "muted"));
  }
}

function persistLocal() {
  try {
    // 이전 버전의 개인정보 캐시도 지운다. 새 프로필은 메모리에만 유지한다.
    localStorage.removeItem(storageKey);
    localStorage.removeItem(legacyKey);
    storageNotice = "";
  } catch {
    storageNotice = "이 기기의 이전 저장값을 지우지 못했어요. 공용 기기라면 브라우저 데이터를 지워주세요.";
  }
}

function serverPayload() {
  return Object.fromEntries(serverFields.map(key => [key, profile[key] || ""]));
}

async function pushProfile() {
  if (!account) return;
  try {
    const data = await request("/api/profile", {method: "PUT", headers: {"Content-Type": "application/json"},
      body: JSON.stringify(serverPayload())});
    if (adoptServerProfile(data.profile)) applyProfile();
    updateAccountUI();
    syncNotice = "";
  } catch (error) {
    syncNotice = `서버 저장 실패: ${error.message} 현재 화면에만 유지돼요. 다시 저장해 주세요.`;
  }
}

function adoptServerProfile(saved) {
  if (!saved || typeof saved !== "object") return false;
  for (const key of [...serverFields, "age"]) {
    if (typeof saved[key] === "string") {
      profile[key] = key === "gender" && saved[key] === "undisclosed" ? "other" : saved[key];
    }
  }
  hasProfile = true;
  document.querySelector("#resume").hidden = false;
  return true;
}

function updateAccountUI() {
  const button = document.querySelector("#account-open");
  button.textContent = account ? profile.nickname || account.username : "로그인";
  button.setAttribute("aria-label", account ? "계정 설정" : "로그인");
  for (const id of ["welcome-login", "welcome-signup", "signup-open"]) {
    document.getElementById(id).hidden = Boolean(account);
  }
  document.querySelector("#start-onboarding").hidden = !account;
  document.querySelector("#resume").hidden = !account || !hasProfile;
  document.querySelector("#welcome-note").textContent = account
    ? "약 1분이면 내 식사 프로필 완성" : "로그인하거나 회원가입하고 내 취향을 알려주세요";
}

function openAccount(mode = "login") {
  cancelRecommendation();
  const current = document.querySelector("[data-screen]:not([hidden])")?.dataset.screen;
  if (current && current !== "account") accountReturn = current;
  document.querySelector("#account-guest").hidden = Boolean(account);
  document.querySelector("#account-member").hidden = !account;
  document.querySelector('[data-screen="account"]').setAttribute("aria-labelledby",
    account ? "account-member-title" : "account-title");
  if (!account) {
    authForm.elements.namedItem("mode").value = mode === "signup" ? "signup" : "login";
    authForm.elements.namedItem("password").value = "";
    setAuthMode();
  }
  if (account) {
    document.querySelector("#account-name").textContent = profile.nickname || account.username;
    document.querySelector("#account-settings").elements.namedItem("nickname").value = profile.nickname;
    document.querySelector("#settings-status").textContent = "";
    document.querySelector("#account-sync").textContent = syncNotice
      || (hasProfile ? "내 식사 프로필이 계정에 저장되어 있어요." : "프로필을 만들면 계정에 자동으로 저장돼요.");
  }
  authStatus.textContent = "";
  showScreen("account");
  document.querySelector(account ? "#account-member-title" : "#account-title").focus({preventScroll: true});
}

function setAuthMode() {
  const signup = authForm.elements.namedItem("mode").value === "signup";
  document.querySelector("#signup-fields").hidden = !signup;
  document.querySelector("#signup-fields").disabled = !signup;
  document.querySelector("#account-title").textContent = signup ? "회원가입" : "로그인";
  document.querySelector("#auth-description").textContent = signup
    ? "내 계정을 만들고, 취향에 맞는 한 끼를 찾아보세요."
    : "로그인하고 저장한 취향으로 메뉴를 찾아보세요.";
  document.querySelector("#auth-submit").replaceChildren(
    document.createTextNode(signup ? "가입하고 시작하기" : "로그인"), node("span", "→"));
  authForm.elements.namedItem("password").autocomplete = signup ? "new-password" : "current-password";
  authStatus.textContent = "";
}

function clearLocalProfile() {
  try {
    localStorage.removeItem(storageKey);
    localStorage.removeItem(legacyKey);
  } catch {
    storageNotice = "이전 브라우저 저장값을 지우지 못했어요.";
  }
  cancelRecommendation();
  profile = {...defaults};
  storageNotice = "";
  hasProfile = false;
  form.reset();
  document.querySelector("#meal").reset();
  chatMessages = [];
  chatConditions = {};
  chatLog.replaceChildren();
  chatChips.replaceChildren();
  results.replaceChildren();
  document.querySelector("#resume").hidden = true;
  return true;
}

authForm.addEventListener("change", event => {
  if (event.target.name === "mode") setAuthMode();
});

authForm.addEventListener("submit", async event => {
  event.preventDefault();
  for (const field of authForm.querySelectorAll("input,select")) {
    if (!field.checkValidity()) {
      field.reportValidity();
      return;
    }
  }
  const values = Object.fromEntries(new FormData(authForm));
  if (values.mode === "signup" && values.password !== values.password_confirm) {
    authStatus.textContent = "비밀번호와 비밀번호 확인이 같지 않아요.";
    return;
  }
  const submit = document.querySelector("#auth-submit");
  submit.disabled = true;
  authStatus.textContent = "";
  try {
    const data = await request(`/api/auth/${values.mode === "signup" ? "signup" : "login"}`, {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({username: values.username, password: values.password,
        ...(values.mode === "signup" ? {profile: Object.fromEntries(
          serverFields.map(key => [key, values[key] || ""]))} : {})}),
    });
    account = data.user;
    clearLocalProfile();
    authForm.reset();
    setAuthMode();
    updateAccountUI();
    syncNotice = "";
    if (adoptServerProfile(data.profile)) {
      persistLocal();
      applyProfile();
      openModes();
    } else {
      startOnboarding();
    }
  } catch (error) {
    authStatus.textContent = error.message;
  } finally {
    submit.disabled = false;
  }
});

document.querySelector("#account-open").addEventListener("click", openAccount);
document.querySelector("#welcome-login").addEventListener("click", openAccount);
document.querySelector("#welcome-signup").addEventListener("click", () => openAccount("signup"));
document.querySelector("#signup-open").addEventListener("click", () => openAccount("signup"));
document.querySelector("#nav-account").addEventListener("click", openAccount);
document.querySelector("#account-edit-profile").addEventListener("click", startOnboarding);
document.querySelector("#account-settings").addEventListener("submit", async event => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector("button");
  const feedback = document.querySelector("#settings-status");
  if (!form.reportValidity()) return;
  button.disabled = true;
  feedback.textContent = "";
  try {
    const data = await request("/api/profile", {method: "PUT", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({nickname: form.elements.namedItem("nickname").value.trim()})});
    adoptServerProfile(data.profile);
    applyProfile();
    updateAccountUI();
    document.querySelector("#account-name").textContent = profile.nickname;
    form.elements.namedItem("nickname").value = profile.nickname;
    feedback.textContent = "닉네임을 변경했어요.";
  } catch (error) {
    feedback.textContent = error.message;
  } finally {
    button.disabled = false;
  }
});
document.querySelector("#account-back").addEventListener("click", () => {
  if (accountReturn === "onboarding") startOnboarding();
  else if (accountReturn === "welcome" || !hasProfile) showScreen("welcome");
  else if (["choice", "chat", "results"].includes(accountReturn)) showScreen(accountReturn);
  else openModes();
});
document.querySelector("#account-continue").addEventListener("click", () => {
  if (hasProfile) openModes();
  else startOnboarding();
});
document.querySelector("#logout").addEventListener("click", async event => {
  const button = event.currentTarget;
  button.disabled = true;
  try {
    await request("/api/auth/logout", {method: "POST"});
  } catch {
    // 서버 연결이 실패해도 이 브라우저의 로그인 표시는 지운다.
  }
  button.disabled = false;
  account = null;
  syncNotice = "";
  clearLocalProfile();
  updateAccountUI();
  authForm.reset();
  setAuthMode();
  showScreen("welcome");
});

form.addEventListener("submit", async event => {
  event.preventDefault();
  if (!account) return openAccount();
  const slide = document.querySelector(`[data-step="${step}"]`);
  for (const field of slide.querySelectorAll("input,select")) {
    if (!field.checkValidity()) {
      field.reportValidity();
      return;
    }
  }
  if (step < 3) {
    step += 1;
    updateStep();
    return;
  }
  const values = Object.fromEntries(new FormData(form));
  for (const key of ["nickname", ...personalFields]) values[key] = values[key].trim();
  for (const key of ["allergy", "exclude"]) {
    if (values[key].trim() && !/[^,;/\s]/u.test(values[key])) {
      profileStatus.textContent = "알레르기와 기피 재료의 이름을 입력해 주세요.";
      return;
    }
  }
  profile = values;
  hasProfile = true;
  persistLocal();
  profileStatus.textContent = "";
  const next = document.querySelector("#step-next");
  next.disabled = true;
  await pushProfile();
  next.disabled = false;
  openModes();
});

document.querySelector("#start-onboarding").addEventListener("click", startOnboarding);
document.querySelector("#resume").addEventListener("click", openModes);
document.querySelector("#step-back").addEventListener("click", () => {
  if (step === 0) {
    if (hasProfile) openModes();
    else showScreen("welcome");
    return;
  }
  step -= 1;
  updateStep();
});
document.querySelector("#skip-body").addEventListener("click", async () => {
  if (step === 0) return;
  for (const key of ["nickname", ...personalFields]) profile[key] = form.elements.namedItem(key).value.trim();
  hasProfile = true;
  await pushProfile();
  openModes();
});
for (const button of document.querySelectorAll("[data-action]")) {
  button.addEventListener("click", () => {
    if (button.dataset.action === "edit-profile") startOnboarding();
    else openModes();
  });
}

document.querySelector("#delete-profile").addEventListener("click", async () => {
  const peek = document.querySelector("#profile-summary");
  if (account) {
    try {
      await request("/api/profile", {method: "DELETE"});
    } catch (error) {
      peek.textContent = `계정에 저장된 프로필을 지우지 못했어요. ${error.message}`;
      return;
    }
  }
  if (!clearLocalProfile()) {
    peek.textContent = "저장소에 접근할 수 없어 삭제하지 못했어요. 브라우저 설정을 확인해 주세요.";
    return;
  }
  syncNotice = "";
  showScreen("welcome");
});

document.querySelector("#open-choice").addEventListener("click", () => {
  sourceMode = "choice";
  const meal = document.querySelector("#meal");
  meal.elements.namedItem("goal").value = profile.goal;
  meal.elements.namedItem("spice").value = profile.spice || "";
  meal.elements.namedItem("soup").value = profile.soup || "";
  document.querySelector("#choice-profile").textContent = summary();
  showScreen("choice");
});

async function recommendNow(conditions, mode) {
  activeRecommendation?.abort();
  const controller = new AbortController();
  activeRecommendation = controller;
  sourceMode = mode;
  setBusy(true);
  if (mode === "choice") {
    showScreen("results");
    status.textContent = "오늘의 메뉴를 비교하고 있어요.";
    results.replaceChildren();
    subtitle.textContent = "";
  } else {
    bubble("좋아요. 알려주신 조건으로 메뉴를 찾아볼게요.");
  }
  try {
    const data = await request("/api/recommend", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({...profilePayload(), ...conditions}), signal: controller.signal,
    });
    if (controller.signal.aborted) return;
    render(data);
    if (mode === "chat") {
      const picked = data.families?.length || data.items.length;
      bubble(data.items.length ? `취향과 조건을 살펴 ${picked}개 메뉴를 골랐어요. 메뉴를 고르면 파는 가게도 알려 드려요.`
        : "지금 조건으로 추천할 메뉴를 찾지 못했어요. 확인이 필요한 정보와 조건을 살펴보세요.");
      const button = node("button", data.items.length ? "추천 메뉴 보러 가기 →" : "결과와 확인 사항 보기 →", "chat-results-link");
      button.type = "button";
      button.addEventListener("click", () => showScreen("results"));
      chatLog.append(button);

      setChatChips(["다시 골라볼래요"]);
    }
  } catch (error) {
    if (controller.signal.aborted) return;
    if (mode === "chat") {
      bubble(error.message);
      setChatChips(["다시 시도"]);
    } else {
      status.textContent = error.message;
      empty("추천을 불러오지 못했어요", "조건을 다시 확인하거나 잠시 뒤 재시도해 주세요.");
    }
  } finally {
    if (activeRecommendation === controller) {
      activeRecommendation = null;
      setBusy(false);
      if (mode === "chat") scrollChat();
    }
  }
}

document.querySelector("#meal").addEventListener("submit", event => {
  event.preventDefault();
  const conditions = Object.fromEntries(new FormData(event.currentTarget));
  // 오늘 고른 음식군은 검색 범위와 선호 점수에 함께 적용한다. 저장 프로필은 유지한다.
  if (conditions.meal_category) conditions.category = conditions.meal_category;
  recommendNow(conditions, "choice");
});
function returnToConditions() {
  if (sourceMode === "chat") showScreen("chat");
  else showScreen("choice");
}
document.querySelector("#results-back").addEventListener("click", returnToConditions);
document.querySelector("#change-conditions").addEventListener("click", returnToConditions);

function scrollChat() {
  requestAnimationFrame(() => { scroller.scrollTop = scroller.scrollHeight; });
}
function bubble(text, who = "bot") {
  chatLog.append(node("div", text, `bubble ${who}`));
  scrollChat();
}
function setChatChips(answers) {
  chatChips.replaceChildren();
  for (const answer of answers) {
    const button = node("button", answer);
    button.type = "button";
    button.addEventListener("click", () => respond(answer));
    chatChips.append(button);
  }
  scrollChat();
}
function startChat() {
  cancelRecommendation();
  sourceMode = "chat";
  chatMessages = [];
  chatConditions = {};
  chatLog.replaceChildren();
  chatInput.value = "";
  chatInput.placeholder = "오늘 먹고 싶은 음식이나 조건을 말해 주세요";
  showScreen("chat");
  bubble("오늘은 어떤 식사가 좋으세요? 원하는 메뉴나 맵기·국물·식사 목표를 자유롭게 이야기해 주세요.");
  setChatChips(["가볍고 안 매운 음식이 좋아요", "국물 있는 메뉴 추천해 줘", "단백질 위주로 먹고 싶어요"]);
}

document.querySelector("#open-chat").addEventListener("click", startChat);
document.querySelector("#restart-chat").addEventListener("click", startChat);

async function respond(answer) {
  if (activeRecommendation || !answer.trim()) return;
  if (!account) return openAccount();
  const text = answer.trim();
  if (text.length > 300) { bubble("300자 이내로 입력해 주세요."); return; }
  const controller = new AbortController();
  activeRecommendation = controller;
  setBusy(true);
  bubble(text, "user");
  chatInput.value = "";
  chatChips.replaceChildren();
  // 입력은 메모리에서만 유지한다. 과거 대화를 조용히 버려 제한 조건이 사라지지 않게 한다.
  if (chatMessages.length >= 6) {
    bubble("대화 6회가 끝났어요. 조건을 정리해 다시 시작해 주세요.");
    activeRecommendation = null;
    setBusy(false);
    return;
  }
  const pending = [...chatMessages, text];
  const waiting = node("div", "AI가 조건과 추천 근거를 살펴보고 있어요…", "bubble bot");
  chatLog.append(waiting);
  scrollChat();
  try {
    const data = await request("/api/chat", {method: "POST",
      headers: {"Content-Type": "application/json"}, signal: controller.signal,
      body: JSON.stringify({messages: pending,
        profile: {...profilePayload(), ...chatConditions, district: document.querySelector("#chat-district").value}}),
    });
    if (controller.signal.aborted) return;
    chatMessages = pending;
    chatConditions = data.conditions;
    render(data.result);
    bubble(data.reply);
    const selected = data.conditions;
    bubble(`적용 조건: ${goalLabels[selected.goal]} · ${selected.spice === "hot" ? "매콤한 맛 선호" : selected.spice === "mild" ? "안 매운 맛 선호" : "맵기 무관"} · ${selected.soup === "yes" ? "국물 선호" : selected.soup === "no" ? "국물 없는 메뉴 선호" : "국물 무관"}${selected.query ? ` · 검색: ${selected.query}` : ""}. 더 바꾸고 싶은 조건을 말해 주세요.`);
    const button = node("button", "추천 결과와 매장 보기 →", "chat-results-link");
    button.type = "button";
    button.addEventListener("click", () => showScreen("results"));
    chatLog.append(button);
  } catch (error) {
    if (!controller.signal.aborted) bubble(error.message);
  } finally {
    waiting.remove();
    if (activeRecommendation === controller) {
      activeRecommendation = null;
      setBusy(false);
      scrollChat();
    }
  }
}

document.querySelector("#chat-form").addEventListener("submit", event => {
  event.preventDefault();
  respond(chatInput.value);
});

// 메뉴 상세의 가게 목록(추천 기준표 v1.3 /api/v2/stores) 항목.
function storeItem(store) {
  const item = node("li");
  const name = node("div", undefined, "store-name");
  name.append(node("span", `${store.store_name} · ${store.district}`),
    node("span", store.rating_display === null ? "별점 미표시" : `★${store.rating_display.toFixed(1)}`, "star"));
  const parts = [store.menu_name, `리뷰 ${store.review_count}개 중 아쉬운 리뷰 ${store.disappointing_reviews}개`];
  if (store.few_reviews) parts.push("리뷰 적음");
  if (store.store_repeat_ratio !== null) parts.push(`이 가게 단골 주문 비율 ${Math.round(store.store_repeat_ratio * 100)}%`);
  if (store.rice_included && store.portion_g) parts.push(`음식 ${Math.round(store.portion_g)}g(추정) + 공깃밥 210g 기준`);
  item.append(name, node("p", parts.join(" · "), "store-meta"));
  for (const note of store.notes) item.append(node("span", note, "tag warn"));
  return item;
}


async function initialize() {
  updateAgeLabels();
  persistLocal();
  try {
    // 로그인 세션이 있으면 계정에 저장된 취향을 우선 적용한다.
    const me = await request("/api/auth/me");
    if (me.user) {
      account = me.user;
      if (adoptServerProfile(me.profile)) persistLocal();
    } else clearLocalProfile();
  } catch {
    // 세션을 확인할 수 없으면 로그인 전 상태를 유지한다.
    clearLocalProfile();
  }
  updateAccountUI();
  applyProfile();
  // 목록으로 정해진 저장값은 화면에 적용된 값을 기준으로 복원한다.
  for (const key of preferenceFields.filter(key => key !== "category")) {
    profile[key] = form.elements.namedItem(key).value;
  }
  // 이미 로그인돼 있고 저장한 취향이 있으면 취향 입력 없이 추천 방식 화면을 연다.
  if (account && hasProfile) openModes();
  try {
    const data = await request("/api/catalog");
    const select = document.querySelector("#category");
    for (const label of data.categories) select.add(new Option(label, label));
    for (const label of data.categories) document.querySelector("#signup-category").add(new Option(label, label));
    const mealCategory = document.querySelector("#meal-category");
    for (const label of data.categories) mealCategory.add(new Option(label, label));
    mealCategory.disabled = false;
    document.querySelector("#meal-category-help").textContent = "음식군을 고르면 그 안에서 추천해요. 선택하지 않으면 저장한 취향을 반영해요.";
    if (!categoryTouched && data.categories.includes(profile.category)) select.value = profile.category;
    if (!data.categories.includes(profile.category)) profile.category = "";
  } catch {
    document.querySelector("#meal-category-help").textContent = "음식군을 불러오지 못했어요. 연결을 확인한 뒤 새로고침해 주세요.";
    // 프로필 작성은 가능하며 실제 추천 시 연결 상태를 안내한다.
  }
}
initialize();
