/* CoFoundr dashboard: progressive enhancement only. Every page works without this file. */
(() => {
  const calm = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];

  /* Loading screen: liquid light on black (WebGL), then the light swells to the dashboard's cream and dissolves. */
  (function splash() {
    /* Loading screen: the van photo, frosted and washed with cream, comes into focus as the dashboard appears on top of it
       (the same photo is the page background, so it reads as one shot). CSS does the motion; this only times the exit. */
    const el = $(".splash"); if (!el || !document.documentElement.classList.contains("splash-on")) return;
    let leaving = false;
    const finish = () => {
      if (leaving) return;
      leaving = true;
      el.classList.add("is-leaving");
      dispatchEvent(new Event("cf:splash-done"));
      setTimeout(() => el.classList.add("is-gone"), 650);
      setTimeout(() => { el.remove(); document.documentElement.classList.remove("splash-on"); }, 1400);
    };
    el.addEventListener("click", finish);
    addEventListener("keydown", finish, { once: true });
    setTimeout(finish, 2200);
  })();

  /* Home greeting typed out letter by letter, once per browser session (or with ?splash=1), after the loading screen.
     The panel keeps its final size while typing; screen readers get the whole greeting at once. */
  (function typeGreeting() {
    const h = $(".hello h1"); if (!h || calm) return;
    let first = true;
    try { first = /[?&]splash=1/.test(location.search) || !sessionStorage.getItem("cf-typed"); sessionStorage.setItem("cf-typed", "1"); } catch (e) {}
    if (!first) return;
    const parts = [...h.childNodes].map((node) => ({ node, text: node.textContent }));
    const full = parts.map((p) => p.text).join("");
    const box = h.getBoundingClientRect();
    h.style.minWidth = `${Math.ceil(box.width)}px`; h.style.minHeight = `${Math.ceil(box.height)}px`;
    h.setAttribute("aria-label", full);
    parts.forEach((p) => { p.node.textContent = ""; });
    const caret = document.createElement("span");
    caret.className = "caret"; caret.setAttribute("aria-hidden", "true"); h.append(caret);
    let part = 0, ch = 0;
    const step = () => {
      const p = parts[part];
      if (!p) { setTimeout(() => caret.classList.add("is-done"), 1400); return; }
      p.node.textContent = p.text.slice(0, ++ch);
      if (ch >= p.text.length) { part += 1; ch = 0; }
      const typed = p.node.textContent.slice(-1);
      setTimeout(step, typed === "," ? 220 : 42 + Math.random() * 38);  // a beat after the comma, like real typing
    };
    const start = () => setTimeout(step, 260);
    if (document.documentElement.classList.contains("splash-on")) addEventListener("cf:splash-done", start, { once: true });
    else start();
  })();

  /* Living backdrop: a slow, light gradient flow (WebGL). Clock = wall time, so it continues across pages. */
  (function backdrop() {
    const canvas = $(".backdrop canvas"); if (!canvas) return;
    const gl = canvas.getContext("webgl", { antialias: false, alpha: false, powerPreference: "low-power" });
    if (!gl) return;
    const vs = "attribute vec2 p;void main(){gl_Position=vec4(p,0.,1.);}";
    const fs = `precision mediump float;uniform vec2 r;uniform float t;uniform vec2 m;
      float h(vec2 p){return fract(sin(dot(p,vec2(127.1,311.7)))*43758.5453);}
      float n(vec2 p){vec2 i=floor(p),f=fract(p),u=f*f*(3.-2.*f);
        return mix(mix(h(i),h(i+vec2(1,0)),u.x),mix(h(i+vec2(0,1)),h(i+vec2(1,1)),u.x),u.y);}
      float fbm(vec2 p){float v=0.,a=.5;for(int i=0;i<5;i++){v+=a*n(p);p=p*2.02+vec2(3.1,1.7);a*=.5;}return v;}
      void main(){
        vec2 uv=gl_FragCoord.xy/r; vec2 p=uv*vec2(r.x/r.y,1.)*1.15; float s=t*.022;
        vec2 q=vec2(fbm(p+vec2(0.,s)),fbm(p+vec2(5.2,1.3)-s));
        vec2 w=vec2(fbm(p+2.6*q+vec2(1.7,9.2)+s*1.3),fbm(p+2.6*q+vec2(8.3,2.8)-s*.9));
        float f=fbm(p+2.2*w);
        vec3 cream=vec3(.957,.945,.922), sand=vec3(.92,.84,.74), apricot=vec3(.96,.74,.56),
             blush=vec3(.92,.76,.76), sage=vec3(.76,.86,.76), sky=vec3(.78,.86,.95);
        vec3 c=mix(cream,sand,smoothstep(.3,.72,f));
        c=mix(c,apricot,smoothstep(.42,.82,w.x)*.8);
        c=mix(c,sage,smoothstep(.46,.86,q.y)*.75);
        c=mix(c,sky,smoothstep(.5,.92,w.y*f*1.7)*.7);
        c=mix(c,blush,smoothstep(.55,.92,q.x*w.y*1.9)*.55);
        c+=.05*smoothstep(.62,.95,f);
        vec2 d=(uv-m)*vec2(r.x/r.y,1.); c=mix(c,vec3(1.),.16*exp(-6.*dot(d,d)));
        gl_FragColor=vec4(c,1.);}`;
    const sh = (type, src) => { const x = gl.createShader(type); gl.shaderSource(x, src); gl.compileShader(x); return x; };
    const prog = gl.createProgram();
    gl.attachShader(prog, sh(gl.VERTEX_SHADER, vs)); gl.attachShader(prog, sh(gl.FRAGMENT_SHADER, fs)); gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) return;
    gl.useProgram(prog);
    gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
    const loc = gl.getAttribLocation(prog, "p"); gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
    const uR = gl.getUniformLocation(prog, "r"), uT = gl.getUniformLocation(prog, "t"), uM = gl.getUniformLocation(prog, "m");
    const mouse = { x: .5, y: .6, tx: .5, ty: .6 };
    let raf = 0;
    const size = () => {
      const k = Math.min(devicePixelRatio || 1, 2) * .35;  // soft by design: low resolution, cheap to draw
      canvas.width = Math.max(1, Math.round(innerWidth * k)); canvas.height = Math.max(1, Math.round(innerHeight * k));
      gl.viewport(0, 0, canvas.width, canvas.height);
    };
    let last = 0;
    const draw = (now = performance.now()) => {
      if (!calm && !document.hidden && now - last < 33) { raf = requestAnimationFrame(draw); return; }  // ~30 fps is plenty
      last = now;
      mouse.x += (mouse.tx - mouse.x) * .07; mouse.y += (mouse.ty - mouse.y) * .07;
      gl.uniform2f(uR, canvas.width, canvas.height); gl.uniform1f(uT, (Date.now() / 1000) % 100000); gl.uniform2f(uM, mouse.x, mouse.y);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
      if (!calm && !document.hidden) raf = requestAnimationFrame(draw);
    };
    size(); draw(); canvas.classList.add("is-live");
    addEventListener("resize", () => { size(); if (calm) draw(); }, { passive: true });
    addEventListener("pointermove", (e) => { mouse.tx = e.clientX / innerWidth; mouse.ty = 1 - e.clientY / innerHeight; }, { passive: true });
    document.addEventListener("visibilitychange", () => { if (!document.hidden) { cancelAnimationFrame(raf); draw(); } });
  })();

  /* 3D tilt + glare on cards (data-tilt = max degrees) */
  if (!calm && matchMedia("(hover: hover)").matches) {
    $$(".tilt").forEach((card) => {
      const max = parseFloat(card.dataset.tilt || 6);
      card.addEventListener("pointermove", (e) => {
        const b = card.getBoundingClientRect(), x = (e.clientX - b.left) / b.width, y = (e.clientY - b.top) / b.height;
        card.classList.add("is-tilting");
        card.style.setProperty("--ry", `${(x - .5) * max * 2}deg`); card.style.setProperty("--rx", `${(.5 - y) * max * 2}deg`);
        card.style.setProperty("--gx", `${x * 100}%`); card.style.setProperty("--gy", `${y * 100}%`);
      }, { passive: true });
      card.addEventListener("pointerleave", () => {
        card.classList.remove("is-tilting"); card.style.setProperty("--rx", "0deg"); card.style.setProperty("--ry", "0deg");
      });
    });
  }

  /* The orb: a ray-marched glass/pearl sphere with a slowly morphing surface, lit by a soft studio environment */
  (function orb() {
    const canvas = $(".orb canvas"); if (!canvas) return;
    const gl = canvas.getContext("webgl", { antialias: true, alpha: true, premultipliedAlpha: true, powerPreference: "low-power" });
    if (!gl) { canvas.parentElement.style.display = "none"; return; }
    const vs = "attribute vec2 p;void main(){gl_Position=vec4(p,0.,1.);}";
    const fs = `precision highp float;uniform vec2 r;uniform float t;uniform vec2 m;
      mat2 rot(float a){float c=cos(a),s=sin(a);return mat2(c,-s,s,c);}
      float map(vec3 p){
        p.xz*=rot(t*.18+m.x*.9); p.yz*=rot(-.25+m.y*.6);
        float d=length(p)-1.;
        d+=.075*sin(p.x*3.2+t*.8)*sin(p.y*2.8+t*.6)*sin(p.z*3.1+t*.7);
        d+=.025*sin(p.x*7.+t*1.3)*sin(p.y*6.+t)*sin(p.z*6.5);
        return d*.7;}
      vec3 nrm(vec3 p){vec2 e=vec2(.0015,0.);return normalize(vec3(map(p+e.xyy)-map(p-e.xyy),map(p+e.yxy)-map(p-e.yxy),map(p+e.yyx)-map(p-e.yyx)));}
      vec3 env(vec3 d){
        float y=d.y*.5+.5;
        vec3 c=mix(vec3(.9,.8,.7),vec3(1.,.98,.95),smoothstep(0.,1.,y));
        c=mix(c,vec3(.97,.7,.48),smoothstep(.15,.95,d.x)*.65);
        c=mix(c,vec3(.7,.82,.97),smoothstep(.1,.95,-d.x)*.55);
        c=mix(c,vec3(.78,.9,.8),smoothstep(.3,1.,-d.y)*.5);
        c+=vec3(1.)*pow(max(0.,dot(d,normalize(vec3(-.45,.75,.5)))),40.)*1.6;
        c+=vec3(1.,.95,.9)*pow(max(0.,dot(d,normalize(vec3(.7,.2,.7)))),18.)*.5;
        return c;}
      void main(){
        vec2 uv=(gl_FragCoord.xy-.5*r)/r.y;
        vec3 ro=vec3(0.,0.,4.6),rd=normalize(vec3(uv,-1.75));
        float tt=0.,d=1.,md=1e9; bool hit=false;
        for(int i=0;i<80;i++){d=map(ro+rd*tt);md=min(md,d);if(d<.0008){hit=true;break;}tt+=d;if(tt>8.)break;}
        if(!hit){float a=smoothstep(.012,0.,md);gl_FragColor=vec4(vec3(.96,.88,.82)*a,a);return;}
        vec3 p=ro+rd*tt,n=nrm(p);
        float fr=pow(1.-max(0.,dot(n,-rd)),2.6);
        vec3 refl=env(reflect(rd,n));
        vec3 body=mix(vec3(.83,.56,.37),vec3(.99,.93,.87),.5+.5*n.y);
        vec3 iri=.5+.5*cos(6.2831*(fr*1.1+vec3(0.,.33,.67))+t*.15);
        vec3 c=mix(body,refl,.58);
        c=mix(c,c*iri*1.25,fr*.45);
        c+=fr*.28;
        c*=.78+.3*max(0.,dot(n,normalize(vec3(-.5,.7,.6))));
        gl_FragColor=vec4(c,1.);}`;
    const sh = (type, src) => { const x = gl.createShader(type); gl.shaderSource(x, src); gl.compileShader(x); return x; };
    const prog = gl.createProgram();
    gl.attachShader(prog, sh(gl.VERTEX_SHADER, vs)); gl.attachShader(prog, sh(gl.FRAGMENT_SHADER, fs)); gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) { canvas.parentElement.style.display = "none"; return; }
    gl.useProgram(prog);
    gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
    const loc = gl.getAttribLocation(prog, "p"); gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
    const uR = gl.getUniformLocation(prog, "r"), uT = gl.getUniformLocation(prog, "t"), uM = gl.getUniformLocation(prog, "m");
    const aim = { x: 0, y: 0, tx: 0, ty: 0 };
    let visible = true, raf = 0;
    const size = () => {
      const k = Math.min(devicePixelRatio || 1, 1.75);
      canvas.width = Math.round(canvas.clientWidth * k); canvas.height = Math.round(canvas.clientHeight * k);
      gl.viewport(0, 0, canvas.width, canvas.height);
    };
    let last = 0;
    const draw = (now = performance.now()) => {
      if (!calm && visible && !document.hidden && now - last < 33) { raf = requestAnimationFrame(draw); return; }  // ~30 fps
      last = now;
      aim.x += (aim.tx - aim.x) * .1; aim.y += (aim.ty - aim.y) * .1;
      gl.uniform2f(uR, canvas.width, canvas.height); gl.uniform1f(uT, (Date.now() / 1000) % 10000); gl.uniform2f(uM, aim.x, aim.y);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
      if (!calm && visible && !document.hidden) raf = requestAnimationFrame(draw);
    };
    size(); draw();
    addEventListener("resize", () => { size(); draw(); }, { passive: true });
    addEventListener("pointermove", (e) => { aim.tx = (e.clientX / innerWidth - .5) * 2; aim.ty = (e.clientY / innerHeight - .5) * 2; }, { passive: true });
    new IntersectionObserver(([e]) => { visible = e.isIntersecting; if (visible) { cancelAnimationFrame(raf); draw(); } }).observe(canvas);
    document.addEventListener("visibilitychange", () => { if (!document.hidden) { cancelAnimationFrame(raf); draw(); } });
  })();

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
  $$(".ask").forEach((card) => card.addEventListener("pointermove", (e) => {
    const b = card.getBoundingClientRect();
    card.style.setProperty("--mx", `${e.clientX - b.left}px`); card.style.setProperty("--my", `${e.clientY - b.top}px`);
  }, { passive: true }));

  /* Forms: busy label on the clicked button, no double submits */
  $$("form").forEach((form) => form.addEventListener("submit", (e) => {
    if (form.matches('[action="/ask"]') && window.fetch) return;  // the chat sends in the background (below)
    if (form.dataset.sent) { e.preventDefault(); return; }
    form.dataset.sent = "1";
    const btn = e.submitter || $("button", form);
    if (btn && btn.dataset.busy) btn.classList.add("is-busy");
    // disable the others only after the browser has captured the clicked button's name/value
    setTimeout(() => $$("button", form).forEach((b) => { if (b !== btn) b.disabled = true; }), 0);
  }));

  /* Chat without reloading: every chat box (Home bar, Chat page, the panel) posts in the background; the panel opens,
     shows the question, a "Thinking…" bubble with moving dots, then swaps in the answer. No JS → normal page loads. */
  (function chat() {
    const panel = $("#chat"); if (!panel || !window.fetch) return;
    const log = $(".chat__log", panel);
    const esc = (s) => s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
    const bold = (s) => esc(s).replace(/\*([^*\n]+)\*/g, "<strong>$1</strong>");  // WhatsApp *bold*, same as the server
    const bubble = (cls, html) => {
      const p = document.createElement("p"); p.className = cls; p.innerHTML = html; log.append(p);
      log.scrollTo({ top: log.scrollHeight, behavior: calm ? "auto" : "smooth" }); return p;
    };
    const open = () => {
      if (!panel.hidden) return;
      panel.hidden = false; panel.classList.add("is-opening");
      log.scrollTop = log.scrollHeight;
      setTimeout(() => panel.classList.remove("is-opening"), 400);
    };
    $(".chat__close", panel).addEventListener("click", (e) => { e.preventDefault(); panel.hidden = true; });
    $$('form[action="/ask"]').forEach((form) => form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const input = $('input[name="text"]', form), text = input.value.trim();
      if (!text || form.dataset.busy) return;
      form.dataset.busy = "1";
      const body = new FormData(form);  // before the box is cleared below
      open();
      bubble("chat__me", esc(text));
      input.value = "";
      const thinking = bubble("chat__them is-thinking", 'Thinking<span class="dots" aria-hidden="true"><i>.</i><i>.</i><i>.</i></span>');
      thinking.setAttribute("aria-label", "Thinking");
      try {
        const res = await fetch("/ask", { method: "POST", body, headers: { Accept: "application/json" } });
        const data = await res.json();
        thinking.innerHTML = bold(data.answer || data.error || "Sorry, something went wrong.");
      } catch (err) {
        thinking.textContent = "Sorry, I couldn't reach CoFoundr just now. Nothing was changed: please try again.";
      }
      thinking.classList.remove("is-thinking"); thinking.removeAttribute("aria-label"); thinking.classList.add("is-new");
      const asked = thinking.previousElementSibling;  // read from the top of the answer, with the question just above it
      log.scrollTo({ top: Math.max(0, (asked || thinking).offsetTop - log.offsetTop - 8), behavior: calm ? "auto" : "smooth" });
      delete form.dataset.busy;
      $("#chat-msg").focus();
    }));
  })();

  /* "Send my edited version" appears only once the draft changes */
  $$(".ask").forEach((card) => {
    const area = $("textarea", card); if (!area) return;
    const original = area.value.trim();
    area.addEventListener("input", () => card.classList.toggle("is-edited", area.value.trim() !== original));
  });

  /* Arriving from a home card ("Pay now" → /approvals#a3): open that approval's details and bring it into view */
  const target = location.hash.length > 1 && document.getElementById(location.hash.slice(1));
  if (target && target.classList.contains("ask")) { const d = $("details", target); if (d) d.open = true; target.scrollIntoView({ block: "center" }); }

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
  const log = $(".chat__log");  // the chat panel opens on the newest message
  if (log) {
    log.scrollTop = log.scrollHeight;
    const url = new URL(location.href); url.searchParams.delete("chat"); url.hash = ""; history.replaceState(null, "", url);
  }
  const toast = $(".toast");
  if (toast) {
    const close = () => { toast.classList.add("is-leaving"); setTimeout(() => toast.remove(), 350); };
    $("button", toast).addEventListener("click", close);
    setTimeout(close, 6500);
    const url = new URL(location.href); url.searchParams.delete("msg"); history.replaceState(null, "", url);
  }

  window.__cofoundr = true;
})();

/* Search box autocomplete (Atlas Search autocomplete via /search/suggest): fills the <datalist>, names only. */
(() => {
  const input = document.querySelector("input[data-suggest]");
  const list = input && document.getElementById(input.getAttribute("list"));
  if (!input || !list) return;
  let timer, last = "";
  input.addEventListener("input", () => {
    clearTimeout(timer);
    const q = input.value.trim();
    if (q.length < 2 || q === last) return;
    timer = setTimeout(async () => {
      last = q;
      try {
        const r = await fetch(`${input.dataset.suggest}?q=${encodeURIComponent(q)}`);
        if (!r.ok) return;
        const { suggestions = [] } = await r.json();
        list.replaceChildren(...suggestions.map(name => Object.assign(document.createElement("option"), { value: name })));
      } catch (_) { /* suggestions are a nicety: the search still works without them */ }
    }, 150);
  });
})();
