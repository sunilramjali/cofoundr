/* CoFoundr dashboard: progressive enhancement only. Every page works (and Saint can read it) without this file. */
(() => {
  const calm = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];

  /* Count-up numbers: data-count="1193" data-prefix="£" */
  $$("[data-count]").forEach((el) => {
    const target = parseFloat(el.dataset.count), prefix = el.dataset.prefix || "", t0 = performance.now(), dur = 900;
    if (!isFinite(target) || calm) return;
    const tick = (now) => {
      const k = Math.min(1, (now - t0) / dur), e = 1 - Math.pow(1 - k, 4);
      el.textContent = prefix + Math.round(target * e).toLocaleString("en-GB");
      if (k < 1) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  });

  /* Soft light that follows the cursor on modules and approval cards */
  $$(".mod, .ask").forEach((card) => card.addEventListener("pointermove", (e) => {
    const b = card.getBoundingClientRect();
    card.style.setProperty("--mx", `${e.clientX - b.left}px`); card.style.setProperty("--my", `${e.clientY - b.top}px`);
  }, { passive: true }));

  /* Forms: busy label on the clicked button, no double submits */
  $$("form").forEach((form) => form.addEventListener("submit", (e) => {
    if (form.dataset.sent) { e.preventDefault(); return; }
    form.dataset.sent = "1";
    const btn = e.submitter || $("button", form);
    if (btn && btn.dataset.busy) btn.classList.add("is-busy");
    // disable the others only after the browser has captured the clicked button's name/value
    setTimeout(() => $$("button", form).forEach((b) => { if (b !== btn) b.disabled = true; }), 0);
  }));

  /* "Send my edited version" appears only once the draft changes */
  $$(".ask").forEach((card) => {
    const area = $("textarea", card); if (!area) return;
    const original = area.value.trim();
    area.addEventListener("input", () => card.classList.toggle("is-edited", area.value.trim() !== original));
  });

  /* Drop zone */
  const zone = $(".drop");
  if (zone) {
    const input = $("input[type=file]", zone), name = $(".drop__file", zone);
    const show = () => { name.textContent = input.files[0] ? input.files[0].name : ""; };
    input.addEventListener("change", show);
    ["dragenter", "dragover"].forEach((ev) => zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.add("is-over"); }));
    ["dragleave", "drop"].forEach((ev) => zone.addEventListener(ev, () => zone.classList.remove("is-over")));
    zone.addEventListener("drop", (e) => { e.preventDefault(); if (e.dataTransfer.files.length) { input.files = e.dataTransfer.files; show(); } });
  }

  /* Copy the inbox address */
  $$("[data-copy]").forEach((btn) => btn.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(btn.dataset.copy); } catch { return; }
    const was = btn.textContent; btn.textContent = "Copied ✓"; setTimeout(() => (btn.textContent = was), 1600);
  }));

  /* Toast: auto-dismiss, and a refresh shouldn't replay it */
  const toast = $(".toast");
  if (toast) {
    const close = () => { toast.classList.add("is-leaving"); setTimeout(() => toast.remove(), 350); };
    $("button", toast).addEventListener("click", close);
    setTimeout(close, 6500);
    const url = new URL(location.href); url.searchParams.delete("msg"); history.replaceState(null, "", url);
  }

  window.__cofoundr = true;
})();
