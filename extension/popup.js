const urlEl = document.getElementById("url");
const onEl = document.getElementById("on");
const row = document.getElementById("row");
const list = document.getElementById("list");

function paint(state) {
  const url = state.url || "";
  urlEl.textContent = url || "Этой страницей управлять нельзя";
  row.style.display = url ? "flex" : "none";
  onEl.checked = state.choice === true;
  onEl.onchange = () => {
    chrome.runtime.sendMessage({ type: "gromkost:choice", url, yes: onEl.checked }, () => {
      chrome.runtime.sendMessage({ type: "gromkost:popup" }, paint);
    });
  };
  list.replaceChildren();
  for (const item of state.enabled || []) {
    const li = document.createElement("li");
    const span = document.createElement("span");
    span.textContent = item;
    const button = document.createElement("button");
    button.textContent = "Убрать";
    button.onclick = () => chrome.runtime.sendMessage({ type: "gromkost:choice", url: item, yes: false }, () => chrome.runtime.sendMessage({ type: "gromkost:popup" }, paint));
    li.append(span, button);
    list.append(li);
  }
}

chrome.runtime.sendMessage({ type: "gromkost:popup" }, paint);
