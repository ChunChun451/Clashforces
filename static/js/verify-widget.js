// The compile-error verification flow, used by login and by handle change.
import { api, el, fmtDuration } from "./api.js";

// startPath: "/api/auth/start" or "/api/me/handle/start". onDone(view) is called once verified.
export function verifyWidget({ startPath, buttonLabel, onDone }) {
  const input = el("input.mono.lg", { type: "text", id: "handle", autocomplete: "username", spellcheck: "false", placeholder: "tourist", required: true });
  const err = el("div.error-box", { hidden: true, role: "alert" });
  const submit = el("button.btn.primary.big.block", { type: "submit" }, buttonLabel);
  const form = el("form.stack", { onsubmit: start },
    el("div.field", el("label", { for: "handle" }, "Codeforces handle"), input),
    err, submit);
  const box = el("div", form);
  let timer = null;

  function showError(msg) { err.textContent = msg; err.hidden = !msg; }

  async function start(e) {
    e.preventDefault();
    showError("");
    submit.disabled = true;
    try {
      const task = await api(startPath, { method: "POST", body: { handle: input.value.trim() } });
      showTask(task);
    } catch (ex) {
      showError(ex.message);
    } finally {
      submit.disabled = false;
    }
  }

  function showTask(task) {
    const left = el("div.kpi.accent", { "aria-live": "off" }, "2:00");
    const status = el("p.muted.small", { role: "status" }, "Waiting for your submission…");
    const again = el("button.btn", { type: "button", onclick: () => { clearInterval(timer); box.replaceChildren(form); } }, "Start over");
    box.replaceChildren(el("div.stack",
      el("p", "To prove you own ", el("strong.mono", task.handle), ", submit any code that ",
        el("strong", "fails to compile"), " on this problem within 2 minutes:"),
      el("div.verify-task.stack",
        el("div.spread",
          el("a.mono", { href: task.problem.url, target: "_blank", rel: "noopener" }, `${task.problem.key} · ${task.problem.name}`),
          left),
        el("p.small.muted", "For example, submit just the word ", el("code", "nope"), " in any language. Compilation errors never count against you on Codeforces.")),
      status, again));

    const tick = () => {
      const rem = task.expires_at - Date.now() / 1000;
      left.textContent = fmtDuration(rem);
      if (rem <= 0) left.classList.remove("accent");
    };
    tick();
    let lastCheck = 0;
    timer = setInterval(async () => {
      tick();
      if (Date.now() - lastCheck < 5000) return;
      lastCheck = Date.now();
      try {
        const view = await api("/api/auth/check", { method: "POST", body: { task_id: task.task_id } });
        if (view.status === "verified") {
          clearInterval(timer);
          status.textContent = "Verified.";
          onDone(view);
        } else if (view.status === "expired") {
          clearInterval(timer);
          status.textContent = "Time ran out without a compilation error. Start over to get a new problem.";
          status.classList.add("danger");
        }
      } catch (ex) {
        status.textContent = ex.message;
      }
    }, 1000);
  }

  return box;
}
