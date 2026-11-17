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
let chatStage = 0;
let chatConditions = {};
let activeRecommendation = null;
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
    dialog.showModal();
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
  document.querySelector("#meal").elements.namedItem("goal").value = profile.goal;
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
      bubble(data.items.length ? `취향과 조건을 살펴 ${data.items.length}개 메뉴를 골랐어요. 추천 이유도 함께 확인해 보세요.`
        : "지금 조건으로 추천할 메뉴를 찾지 못했어요. 확인이 필요한 정보와 조건을 살펴보세요.");
      const button = node("button", data.items.length ? "추천 메뉴 보러 가기 →" : "결과와 확인 사항 보기 →", "chat-results-link");
      button.type = "button";
      button.addEventListener("click", () => showScreen("results"));
      chatLog.append(button);
      chatStage = 3;
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
  chatStage = 0;
  chatConditions = {goal: profile.goal, spice: profile.spice, soup: profile.soup, query: ""};
  chatLog.replaceChildren();
  chatInput.value = "";
  chatInput.placeholder = "아래 선택지로 답해도 좋아요";
  showScreen("chat");
  bubble(`${profile.nickname || "메뉴고민러"}님, 반가워요! 저장된 취향을 기억하고 있어요. 오늘은 어떤 식사가 좋을까요?`);
  setChatChips(["균형 있게 먹을래요", "단백질 위주로요", "가볍게 먹고 싶어요"]);
}
document.querySelector("#open-chat").addEventListener("click", startChat);
document.querySelector("#restart-chat").addEventListener("click", startChat);

function respond(answer) {
  if (activeRecommendation || !answer.trim()) return;
  const text = answer.trim();
  bubble(text, "user");
  chatInput.value = "";
  if (text === "다시 골라볼래요") {
    startChat();
    return;
  }
  if (text === "다시 시도") {
    recommendNow({...chatConditions, district: document.querySelector("#chat-district").value}, "chat");
    return;
  }
  if (chatStage === 0) {
    if (/단백질/u.test(text)) chatConditions.goal = "protein";
    else if (/가볍/u.test(text)) chatConditions.goal = "light";
    else if (/균형/u.test(text)) chatConditions.goal = "balance";
    else {
      bubble("균형 식사, 단백질 중심, 가벼운 식사 중 하나로 답해 주세요.");
      return;
    }
    chatStage = 1;
    bubble("좋아요! 오늘 끌리는 맛이나 형태가 있나요?");
    setChatChips(["국물 있는 메뉴", "매콤한 메뉴", "안 매운 메뉴", "프로필 취향대로"]);
    return;
  }
  if (chatStage === 1) {
    if (/국물/u.test(text)) chatConditions.soup = "yes";
    else if (/안 매운|안매운/u.test(text)) chatConditions.spice = "mild";
    else if (/매콤|매운/u.test(text)) chatConditions.spice = "hot";
    else if (!/프로필|상관없/u.test(text)) {
      bubble("아래 선택지에서 오늘의 취향을 골라주세요.");
      return;
    }
    chatStage = 2;
    bubble("마지막으로 먹고 싶은 메뉴가 있나요? 메뉴 이름을 적거나, 바로 추천받기를 눌러주세요.");
    chatInput.placeholder = "메뉴명 입력 · 예. 비빔밥";
    setChatChips(["비빔밥", "국밥", "바로 추천받기"]);
    return;
  }
  if (chatStage === 3) {
    bubble("새로운 조건으로 골라볼까요? 다시 시작을 눌러주세요.");
    return;
  }
  const query = text === "바로 추천받기" ? "" : text;
  if (query.length > 40) {
    bubble("찾고 싶은 메뉴명을 40자 이내로 적어주세요.");
    return;
  }
  chatConditions.query = query;
  chatChips.replaceChildren();
  recommendNow({...chatConditions, district: document.querySelector("#chat-district").value}, "chat");
}
document.querySelector("#chat-form").addEventListener("submit", event => {
  event.preventDefault();
  respond(chatInput.value);
});

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
