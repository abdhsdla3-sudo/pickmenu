"use strict";

const form = document.querySelector("#profile");
const status = document.querySelector("#result-status");
const profileStatus = document.querySelector("#profile-status");
const results = document.querySelector("#results");
const subtitle = document.querySelector("#results-sub");
const dialog = document.querySelector("#detail");
const submit = form.querySelector('[type="submit"]');
const resetButton = document.querySelector("#reset-profile");
const storageKey = "pickmenu.profile.v1";
const fields = ["diet", "goal", "spice", "soup", "category", "exclude", "allergy"];
const labels = ["열량", "단백질", "탄수화물", "지방", "나트륨"];
const units = ["kcal", "g", "g", "g", "mg"];

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
    throw new Error("로컬 서버에 연결할 수 없어요. 서버 실행 상태를 확인해 주세요.");
  }
  if (!response.ok) throw new Error(data.error || "요청을 처리할 수 없어요.");
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

form.addEventListener("submit", async event => {
  event.preventDefault();
  submit.disabled = true;
  resetButton.disabled = true;
  status.textContent = "수원 메뉴를 비교하고 있어요.";
  results.replaceChildren();
  try {
    const data = await request("/api/recommend", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(Object.fromEntries(new FormData(form))),
    });
    render(data);
  } catch (error) {
    subtitle.textContent = "조건과 데이터 상태를 확인해 주세요.";
    status.textContent = error.message;
    empty("추천을 불러오지 못했어요", "입력을 확인하고 다시 시도해 주세요.");
  } finally {
    submit.disabled = false;
    resetButton.disabled = false;
  }
});

document.querySelector("#save").addEventListener("click", () => {
  try {
    const values = Object.fromEntries(fields.map(key => [key, form.elements.namedItem(key).value]));
    localStorage.setItem(storageKey, JSON.stringify(values));
    profileStatus.textContent = "이 브라우저에 프로필을 저장했어요.";
  } catch {
    profileStatus.textContent = "브라우저 저장소를 사용할 수 없어요.";
  }
});

resetButton.addEventListener("click", () => {
  form.reset();
  try {
    localStorage.removeItem(storageKey);
    profileStatus.textContent = "프로필과 이번 식사 조건을 초기화했어요.";
  } catch {
    profileStatus.textContent = "입력은 초기화했어요. 브라우저 저장소는 확인이 필요해요.";
  }
  status.textContent = "";
  subtitle.textContent = "조건을 선택해 내 식사에 맞는 메뉴를 찾아보세요.";
  empty("추천 메뉴가 여기에 표시돼요", "메뉴마다 추천 근거와 참고 영양정보를 확인할 수 있어요.");
});

async function initialize() {
  submit.disabled = true;
  try {
    const data = await request("/api/catalog");
    const select = document.querySelector("#category");
    for (const label of data.categories) select.add(new Option(label, label));
  } catch (error) {
    status.textContent = error.message;
  } finally {
    submit.disabled = false;
  }
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey) || "{}");
    for (const key of fields) {
      if (typeof saved[key] !== "string") continue;
      const field = form.elements.namedItem(key);
      if (field instanceof HTMLSelectElement && !Array.from(field.options).some(option => option.value === saved[key])) continue;
      field.value = saved[key];
    }
  } catch {
    profileStatus.textContent = "저장된 프로필을 읽을 수 없어요.";
  }
}

initialize();
