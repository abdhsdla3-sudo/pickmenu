"use strict";
// 픽메뉴 관리자 화면(1.14.0). 모든 자료는 /api/admin/*에서 실시간으로 읽는다(하드코딩 없음).

const panel = document.querySelector("#panel");
const FIELD = {goal: "영양 목표", diet: "식단", spice: "맵기", soup: "국물", query: "메뉴 검색어",
  meal_category: "음식군", category: "선호 음식군", exclude: "제외", allergy: "알레르기", taste: "맛",
  avoid_menus: "피할 메뉴", exclude_categories: "뺄 음식군", done: "대화 마침"};
const REVIEW = {unchecked: "미검수", correct: "맞음", wrong: "틀림"};
const STATE = {new: "접수", in_progress: "처리 중", done: "완료"};
const KIND = {bad_recommendation: "추천이 별로예요", error: "오류 신고", idea: "의견"};
const STATUS = {ok: "성공", busy: "대기 초과", off: "AI 꺼짐", error: "오류", timeout: "시간 초과"};

function el(tag, text, className) {
  const item = document.createElement(tag);
  if (text !== undefined && text !== null) item.textContent = String(text);
  if (className) item.className = className;
  return item;
}

function when(ts) {
  if (!ts) return "";
  return new Date(ts * 1000).toLocaleString("ko-KR", {timeZone: "Asia/Seoul", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit"});
}

async function api(url, options = {}) {
  const response = await fetch(url, {...options, headers: {"Content-Type": "application/json"}});
  let data = {};
  try {
    data = await response.json();
  } catch {
    // 빈 응답
  }
  if (!response.ok) {
    const error = new Error(data.error || "요청을 처리하지 못했어요.");
    error.status = response.status;
    throw error;
  }
  return data;
}

function table(headers, rows) {
  const wrap = el("table");
  const head = el("tr");
  headers.forEach(name => head.append(el("th", name)));
  wrap.append(head);
  rows.forEach(cells => {
    const tr = el("tr");
    cells.forEach(cell => {
      const td = el("td");
      if (cell instanceof Node) td.append(cell);
      else td.textContent = cell ?? "";
      tr.append(td);
    });
    wrap.append(tr);
  });
  return wrap;
}

function bar(value, max, alt = false) {
  const outer = el("div");
  const inner = el("div", undefined, alt ? "bar alt" : "bar");
  inner.style.width = `${max ? Math.round((value / max) * 100) : 0}%`;
  outer.append(inner);
  return outer;
}

function kpi(value, label) {
  const box = el("div", undefined, "kpi");
  box.append(el("b", value ?? "–"), el("span", label));
  return box;
}

function conditionChips(conditions) {
  const chips = el("div", undefined, "chips");
  for (const [key, value] of Object.entries(conditions || {})) {
    chips.append(el("span", `${FIELD[key] || key}: ${value === true ? "예" : value}`, "chip"));
  }
  if (!chips.childElementCount) chips.append(el("span", "조건 없음", "chip warn"));
  return chips;
}

// ---------- 사용 현황 ----------
async function dashboard(days = 14) {
  const data = await api(`/api/admin/summary?days=${days}`);
  panel.replaceChildren();
  const tools = el("div", undefined, "toolbar");
  const pick = el("select");
  for (const value of [7, 14, 30]) {
    const option = el("option", `최근 ${value}일`);
    option.value = value;
    option.selected = value === data.days;
    pick.append(option);
  }
  pick.addEventListener("change", () => dashboard(Number(pick.value)));
  tools.append(pick, el("span", "모든 수치는 실제 사용 기록에서 바로 계산해요. 시각은 한국 시간이에요.", "muted"));
  panel.append(tools);

  const t = data.totals;
  const kpis = el("div", undefined, "kpis");
  kpis.append(kpi(t.visitors, "방문자(브라우저)"), kpi(t.returning, "재방문자"),
    kpi(t.recommend, "선택지 추천"), kpi(t.chat, "이야기 추천"), kpi(t.menu_click, "메뉴 클릭"),
    kpi(t.store_click, "가게 상세 클릭"));
  panel.append(el("h2", "사용 지표"), kpis);

  const ai = data.ai;
  const aiKpis = el("div", undefined, "kpis");
  aiKpis.append(kpi(ai.calls, "AI 호출"), kpi(ai.avg_ms === null ? "–" : `${ai.avg_ms}ms`, "평균 응답"),
    kpi(ai.p95_ms === null ? "–" : `${ai.p95_ms}ms`, "느린 5% 응답"),
    kpi(ai.calls ? `${Math.round((ai.errors / ai.calls) * 100)}%` : "–", `오류·대기 초과 ${ai.errors}건`),
    kpi((ai.prompt_tokens + ai.output_tokens).toLocaleString("ko-KR"), "토큰(입력+출력)"));
  panel.append(el("h2", "AI 운영"), aiKpis);

  const daily = el("div", undefined, "card");
  const max = Math.max(1, ...data.daily.map(row => row.recommend + row.chat));
  const maxVisit = Math.max(1, ...data.daily.map(row => row.visitors));
  daily.append(table(["날짜", "방문자", "", "추천(선택지+이야기)", "", "메뉴 클릭"],
    data.daily.map(row => [row.day, row.visitors, bar(row.visitors, maxVisit, true),
      `${row.recommend}+${row.chat}`, bar(row.recommend + row.chat, max), row.menu_click])));
  panel.append(el("h2", "날짜별"), daily);

  const two = el("div", undefined, "grid2");
  const menus = el("div", undefined, "card");
  menus.append(el("h2", "많이 고른 메뉴"), data.top_menus.length
    ? table(["메뉴", "클릭"], data.top_menus) : el("p", "아직 기록이 없어요.", "muted"));
  const searches = el("div", undefined, "card");
  searches.append(el("h2", "많이 찾은 검색어·음식군"), data.top_searches.length
    ? table(["검색어", "횟수"], data.top_searches) : el("p", "아직 기록이 없어요.", "muted"));
  const status = el("div", undefined, "card");
  status.append(el("h2", "처리할 일"), table(["항목", "건수"], [
    ["피드백 접수", data.feedback.new], ["피드백 처리 중", data.feedback.in_progress],
    ["AI 결과 미검수", data.reviews.unchecked], ["AI 결과 틀림", data.reviews.wrong]]));
  two.append(menus, searches, status);
  panel.append(two);
}

// ---------- AI 결과 검수 ----------
async function chats(status = "") {
  const data = await api(`/api/admin/chats?status=${status}`);
  panel.replaceChildren();
  const tools = el("div", undefined, "toolbar");
  const pick = el("select");
  for (const [value, label] of [["", "전체"], ...Object.entries(REVIEW)]) {
    const option = el("option", label);
    option.value = value;
    option.selected = value === status;
    pick.append(option);
  }
  pick.addEventListener("change", () => chats(pick.value));
  const exportButton = el("button", "틀림 대화 내보내기(회귀 시험 후보)");
  exportButton.type = "button";
  exportButton.addEventListener("click", async () => {
    const wrong = await api("/api/admin/chats?status=wrong");
    const blob = new Blob([wrong.chats.map(chat => JSON.stringify({messages: chat.messages,
      got: chat.conditions, note: chat.note})).join("\n")], {type: "application/json"});
    const link = el("a");
    link.href = URL.createObjectURL(blob);
    link.download = "pickmenu-wrong-chats.jsonl";
    link.click();
    URL.revokeObjectURL(link.href);
  });
  tools.append(pick, exportButton,
    el("span", `이야기 추천 대화를 ${data.keep_days}일 동안 보관해요(아이디 없음).`, "muted"));
  panel.append(tools);
  if (!data.chats.length) panel.append(el("p", "검수할 대화가 없어요.", "muted"));
  for (const chat of data.chats) {
    const card = el("div", undefined, "card");
    const head = el("div", undefined, "chips");
    head.append(el("span", `#${chat.id} · ${when(chat.at)}`, "chip"),
      el("span", REVIEW[chat.status], chat.status === "wrong" ? "chip warn" : chat.status === "correct" ? "chip ok" : "chip"));
    if (chat.clarified) head.append(el("span", "되물음", "chip"));
    const list = el("ol", undefined, "msgs");
    chat.messages.forEach(message => list.append(el("li", message)));
    card.append(head, list, el("strong", "이번 대화로 바뀐 조건(서버 해석 후)"), conditionChips(chat.conditions));
    if (chat.menus.length) card.append(el("p", `추천 메뉴: ${chat.menus.join(", ")}`, "muted"));
    const raw = el("details");
    raw.append(el("summary", "AI 원래 출력 보기"), el("pre", chat.ai_output ? JSON.stringify(chat.ai_output, null, 1)
      : "AI 호출 없음(이전 결과 재사용 또는 규칙으로 처리)"));
    card.append(raw);
    const row = el("div", undefined, "row");
    const note = el("input");
    note.type = "text";
    note.maxLength = 300;
    note.placeholder = "검수 메모 (예: 순대국밥은 이미 먹은 음식인데 추천됨)";
    note.value = chat.note;
    const saved = el("span", "", "saved");
    for (const [value, label] of [["correct", "맞음"], ["wrong", "틀림"], ["unchecked", "미검수로"]]) {
      const button = el("button", label);
      button.type = "button";
      button.addEventListener("click", async () => {
        try {
          await api(`/api/admin/chats/${chat.id}`, {method: "POST", body: JSON.stringify({status: value, note: note.value})});
          saved.textContent = `저장했어요: ${REVIEW[value]}`;
        } catch (error) {
          saved.textContent = error.message;
        }
      });
      row.append(button);
    }
    row.append(note, saved);
    card.append(row);
    panel.append(card);
  }
}

// ---------- 피드백 ----------
async function feedback(status = "") {
  const data = await api(`/api/admin/feedback?status=${status}`);
  panel.replaceChildren();
  const tools = el("div", undefined, "toolbar");
  const pick = el("select");
  for (const [value, label] of [["", "전체"], ...Object.entries(STATE)]) {
    const option = el("option", label);
    option.value = value;
    option.selected = value === status;
    pick.append(option);
  }
  pick.addEventListener("change", () => feedback(pick.value));
  tools.append(pick, el("span", "사용자 하단의 ‘피드백·신고’와 추천 화면에서 들어온 내용이에요.", "muted"));
  panel.append(tools);
  if (!data.feedback.length) panel.append(el("p", "들어온 의견이 없어요.", "muted"));
  for (const item of data.feedback) {
    const card = el("div", undefined, "card");
    const head = el("div", undefined, "chips");
    head.append(el("span", `#${item.id} · ${when(item.at)}`, "chip"), el("span", KIND[item.kind] || item.kind,
      item.kind === "error" ? "chip warn" : "chip"), el("span", STATE[item.status], item.status === "done" ? "chip ok" : "chip"));
    card.append(head, el("p", item.message || "(내용 없음)"));
    const context = item.context || {};
    card.append(el("p", `방식: ${context.mode === "chat" ? "이야기" : "선택지"} · 조건: ${context.conditions || "-"} · `
      + `추천 메뉴: ${(context.menus || []).join(", ") || "-"}`, "muted"));
    if (item.updated_by) card.append(el("p", `마지막 처리: ${item.updated_by} · ${when(item.updated_at)}`, "muted"));
    const row = el("div", undefined, "row");
    const state = el("select");
    for (const [value, label] of Object.entries(STATE)) {
      const option = el("option", label);
      option.value = value;
      option.selected = value === item.status;
      state.append(option);
    }
    const note = el("input");
    note.type = "text";
    note.maxLength = 300;
    note.placeholder = "처리 메모";
    note.value = item.note;
    const save = el("button", "저장");
    save.type = "button";
    const saved = el("span", "", "saved");
    save.addEventListener("click", async () => {
      try {
        await api(`/api/admin/feedback/${item.id}`, {method: "POST", body: JSON.stringify({status: state.value, note: note.value})});
        saved.textContent = "저장했어요.";
      } catch (error) {
        saved.textContent = error.message;
      }
    });
    row.append(state, note, save, saved);
    card.append(row);
    panel.append(card);
  }
}

// ---------- AI 운영 로그 ----------
async function aiLog() {
  const data = await api("/api/admin/ai-calls");
  panel.replaceChildren(el("p", "AI(파인튜닝 Qwen3-8B, 로컬 GPU)를 부를 때마다 남기는 기록이에요. 로컬 GPU라 외부 API 요금은 없고, "
    + "비용은 토큰 수로 봐요. 최근 100건.", "muted"));
  const card = el("div", undefined, "card");
  card.append(table(["시각", "용도", "모델", "응답(ms)", "입력 토큰", "출력 토큰", "결과", "오류"],
    data.calls.map(call => [when(call.at), call.purpose, call.model, call.ms, call.prompt_tokens ?? "-",
      call.output_tokens ?? "-", STATUS[call.status] || call.status, call.error])));
  panel.append(card);
}

// ---------- 메뉴 관리 ----------
async function menus(query = "") {
  const data = await api(`/api/admin/menus?q=${encodeURIComponent(query)}`);
  panel.replaceChildren();
  const tools = el("form", undefined, "toolbar");
  const search = el("input");
  search.placeholder = "메뉴 이름·대표음식·메뉴 ID";
  search.value = query;
  search.maxLength = 40;
  const go = el("button", "찾기");
  tools.append(search, go, el("span", query ? "최대 50개" : "검색어가 없으면 수정한 메뉴만 보여요.", "muted"));
  tools.addEventListener("submit", event => {
    event.preventDefault();
    menus(search.value.trim());
  });
  panel.append(el("p", "숨긴 메뉴는 추천과 메뉴 상세에서 빠지고, 표시 이름은 화면의 메뉴 이름을 바꿔요. 원본 카탈로그는 바꾸지 않고, "
    + "모든 수정은 변경 이력에 남아요.", "muted"), tools);
  const card = el("div", undefined, "card");
  card.append(table(["메뉴", "대표음식·음식군", "영양", "숨김", "표시 이름", "메모", ""],
    data.menus.map(menu => {
      const hidden = el("input");
      hidden.type = "checkbox";
      hidden.checked = menu.hidden;
      hidden.setAttribute("aria-label", `${menu.name} 숨기기`);
      const display = el("input");
      display.type = "text";
      display.maxLength = 60;
      display.value = menu.display_name;
      display.placeholder = "그대로";
      const note = el("input");
      note.type = "text";
      note.maxLength = 200;
      note.value = menu.note;
      const cell = el("div");
      const save = el("button", "저장");
      save.type = "button";
      const saved = el("span", "", "saved");
      save.addEventListener("click", async () => {
        try {
          await api(`/api/admin/menus/${menu.standard_menu_id}`, {method: "POST",
            body: JSON.stringify({hidden: hidden.checked, display_name: display.value, note: note.value})});
          saved.textContent = " 저장됨";
        } catch (error) {
          saved.textContent = ` ${error.message}`;
        }
      });
      cell.append(save, saved);
      const name = el("div");
      name.append(el("div", menu.name), el("div", menu.standard_menu_id, "muted"));
      return [name, `${menu.food} · ${menu.category}`, menu.nutrition_status, hidden, display, note, cell];
    })));
  if (!data.menus.length) card.append(el("p", query ? "찾은 메뉴가 없어요." : "아직 수정한 메뉴가 없어요.", "muted"));
  panel.append(card);
}

// ---------- 변경 이력 ----------
async function audit() {
  const data = await api("/api/admin/audit");
  panel.replaceChildren(el("p", "관리자가 바꾼 내용(메뉴 수정, 검수, 피드백 처리, 권한)을 최근 100건 보여 줘요.", "muted"));
  const card = el("div", undefined, "card");
  card.append(table(["시각", "관리자", "작업", "대상", "이전", "이후"],
    data.audit.map(row => [when(row.at), row.actor, row.action, row.target, row.before, row.after])));
  panel.append(card);
}

const TABS = {dashboard, chats, feedback, ai: aiLog, menus, audit};

async function openTab(name) {
  for (const button of document.querySelectorAll("[data-tab]")) button.classList.toggle("on", button.dataset.tab === name);
  panel.replaceChildren(el("p", "불러오는 중…", "muted"));
  try {
    await TABS[name]();
  } catch (error) {
    if (error.status === 401 || error.status === 403) return start();
    panel.replaceChildren(el("p", error.message, "error"));
  }
}

function show(id) {
  for (const section of ["login", "denied", "app"]) document.querySelector(`#${section}`).hidden = section !== id;
}

async function start() {
  try {
    const me = await api("/api/admin/me");
    document.querySelector("#who").textContent = `${me.user} 관리자`;
    document.querySelector("#logout").hidden = false;
    show("app");
    openTab("dashboard");
  } catch (error) {
    if (error.status === 403) {
      document.querySelector("#denied-text").textContent = error.message;
      document.querySelector("#logout").hidden = false;
      show("denied");
    } else show("login");
  }
}

document.querySelector("#login-form").addEventListener("submit", async event => {
  event.preventDefault();
  const form = event.currentTarget;
  const status = document.querySelector("#login-status");
  status.textContent = "";
  try {
    await api("/api/auth/login", {method: "POST", body: JSON.stringify({username: form.username.value.trim(),
      password: form.password.value})});
    form.password.value = "";
    start();
  } catch (error) {
    status.textContent = error.message;
  }
});
document.querySelector("#logout").addEventListener("click", async () => {
  await api("/api/auth/logout", {method: "POST"}).catch(() => {});
  document.querySelector("#who").textContent = "";
  document.querySelector("#logout").hidden = true;
  show("login");
});
for (const button of document.querySelectorAll("[data-tab]")) button.addEventListener("click", () => openTab(button.dataset.tab));
start();
