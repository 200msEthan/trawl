// Browser tests for the playground: whatever you click, everything on screen must belong to
// the example that is showing and to the run that is happening now.
//
//   cd tests/playground && npm install && npx playwright install chromium   (once)
//   node tests/playground/state.test.js
//
// Set PYODIDE_DIR to a folder with Pyodide 0.26.4's files to run without the network.
// Passing prints "ok: N checks".
const { chromium } = require("playwright");
const fs = require("fs"), path = require("path");

const PAGE = "file://" + path.resolve(__dirname, "../../playground/index.html");
let checks = 0, failures = 0;
function expect(ok, what) { checks++; if (!ok) { failures++; console.log("FAIL:", what); } }

async function open(browser, hash = "") {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  if (process.env.PYODIDE_DIR) {
    await ctx.route("https://cdn.jsdelivr.net/pyodide/v0.26.4/full/**", (r) => {
      const f = path.join(process.env.PYODIDE_DIR, new URL(r.request().url()).pathname.split("/").pop());
      const type = f.endsWith(".wasm") ? "application/wasm" : f.endsWith(".json") ? "application/json" : "text/javascript";
      r.fulfill({ body: fs.readFileSync(f), contentType: type, headers: { "access-control-allow-origin": "*" } });
    });
  }
  const p = await ctx.newPage();
  p.errors = [];
  p.on("pageerror", (e) => p.errors.push(e.message));
  await p.goto(PAGE + hash);
  return p;
}
const T = { timeout: 180000 };
const idle = (p) => p.waitForFunction(() => !running && ready && catchQueue.length === 0
  && document.getElementById("run").textContent === "Run", null, T).then(() => p.waitForTimeout(400));
const introOver = (p) => p.waitForFunction(() => !intro.on, null, T);
const pick = (p, id) => p.selectOption("#examples", id);
const says = (p) => p.$$eval("#catches .catch:not(.find) .say", (els) => els.map((e) => e.textContent).reverse());
const setKnob = (p, i, v) => p.$eval(`#knob${i}`, (r, v) => { r.value = v; r.dispatchEvent(new Event("input")); }, v);

// everything on screen agrees with the example `id`, and with a finished run of its code as it stands
async function consistent(p, id, label, fingerprint) {
  const s = await p.evaluate(() => ({
    pick: document.getElementById("examples").value,
    title: document.getElementById("about-title").textContent,
    knobs: [...document.querySelectorAll("#knobs .knob:not([hidden]) label")].map((l) => l.textContent),
    status: document.getElementById("status-text").textContent,
    note: !document.getElementById("callout").hidden && document.getElementById("caption").textContent,
    now: document.getElementById("now").textContent,
    count: +document.getElementById("count").textContent.replace(/,/g, ""),
    shown: document.querySelectorAll("#catches .catch:not(.find)").length,
    long: [...document.querySelectorAll("#catches .say")].some((e) =>
      [...e.childNodes].some((n) => n.nodeType === 3 && /\d{25}/.test(n.textContent))),
    run: document.getElementById("run").textContent,
  }));
  const ex = await p.evaluate((id) => EXAMPLES.find((e) => e.id === id), id);
  expect(s.pick === id, `${label}: picker shows ${s.pick}, not ${id}`);
  expect(s.title === ex.title, `${label}: about box says "${s.title}", not "${ex.title}"`);
  const want = (ex.knobs || []).map((k) => k[0]);
  expect(JSON.stringify(s.knobs) === JSON.stringify(want), `${label}: sliders ${JSON.stringify(s.knobs)}, want ${JSON.stringify(want)}`);
  expect(/^done|^stopped with an error/.test(s.status), `${label}: status "${s.status}" after the run ended`);
  expect(!(s.note && s.note.startsWith("Still hunting")), `${label}: "Still hunting" note shown after the run ended (${s.status})`);
  expect(s.now === "", `${label}: "${s.now}" still shown after the run ended`);
  expect(s.count === Math.min(s.count, 40) ? s.shown === s.count : s.shown === 40, `${label}: counter says ${s.count}, ${s.shown} cards shown`);
  expect(!s.long, `${label}: a catch shows a huge number in full`);
  expect(s.run === "Run", `${label}: button says ${s.run}`);
  if (fingerprint) {
    const got = await says(p);
    expect(JSON.stringify(got) === JSON.stringify(fingerprint),
      `${label}: catches don't match a clean run of ${id}:\n   got  ${JSON.stringify(got).slice(0, 300)}\n   want ${JSON.stringify(fingerprint).slice(0, 300)}`);
  }
  expect(p.errors.length === 0, `${label}: page errors ${p.errors.join("; ")}`);
}

(async () => {
  const browser = await chromium.launch();
  const p = await open(browser);
  const ids = await p.evaluate(() => EXAMPLES.map((e) => e.id));
  const fast = ids.filter((id) => !["collatz", "k4"].includes(id));

  // 1. the guided intro plays out, then the Collatz hunt it showed is what's on screen
  await introOver(p); await idle(p);
  const collatz = await says(p);
  expect(collatz.length > 5, "intro: Collatz found records");
  expect((await p.$$("#catches .catch.find")).length === 3, "intro: the three Erdős finds are shown");
  await consistent(p, "collatz", "after the intro", collatz);

  // 2. every example, in turn, from a finished run: a clean fingerprint of each
  const prints = { collatz };
  for (const id of ids.filter((i) => i !== "collatz")) {
    await pick(p, id); await idle(p);
    prints[id] = await says(p);
    await consistent(p, id, `switch to ${id}`);
  }
  // and back through them in another order: the same catches every time, nothing carried over
  for (const id of [...fast].reverse()) { await pick(p, id); await idle(p); await consistent(p, id, `back to ${id}`, prints[id]); }

  // 3. switching in the middle of a long hunt: the old hunt stops and none of its catches stay
  for (const id of fast) {
    await pick(p, "collatz"); await setKnob(p, 0, 100000); await p.waitForTimeout(1500);
    await pick(p, id); await idle(p);
    await consistent(p, id, `switch to ${id} mid-hunt`, prints[id]);
  }

  // 4. a slider, then a switch before that hunt even starts
  await pick(p, "hello"); await idle(p);
  await setKnob(p, 0, 3000); await pick(p, "queens"); await idle(p);
  await consistent(p, "queens", "slider then switch", prints.queens);

  // 5. Stop, then switch while the interpreter restarts
  await pick(p, "collatz"); await setKnob(p, 0, 100000); await p.waitForTimeout(1500);
  await p.click("#run"); await pick(p, "reef"); await idle(p);
  await consistent(p, "reef", "stop then switch", prints.reef);
  // Stop then the same example's Run
  await pick(p, "collatz"); await setKnob(p, 0, 100000); await p.waitForTimeout(1500);
  await p.click("#run"); await p.waitForFunction(() => ready, null, T); await pick(p, "orbit"); await idle(p);
  await consistent(p, "orbit", "stop, wait, then switch", prints.orbit);

  // 6. the "Still hunting" note belongs to its run
  await pick(p, "collatz"); await setKnob(p, 0, 100000);
  await p.waitForFunction(() => !document.getElementById("callout").hidden
    && document.getElementById("caption").textContent.startsWith("Still hunting"), null, { timeout: 30000 });
  expect(true, "note: shows on a long hunt");
  await pick(p, "hello"); await idle(p);
  await consistent(p, "hello", "note, then switch to a 1 ms example", prints.hello);
  await pick(p, "collatz"); await setKnob(p, 0, 100000);
  await p.waitForFunction(() => document.getElementById("caption").textContent.startsWith("Still hunting"), null, { timeout: 30000 });
  await p.click("#run"); await p.waitForFunction(() => ready, null, T); await p.waitForTimeout(500);
  expect(await p.$eval("#callout", (c) => c.hidden), "note: gone after Stop");

  // 7. huge numbers are shortened, and a tap shows them in full
  await pick(p, "hello"); await setKnob(p, 0, 3000); await idle(p);
  await consistent(p, "hello", "2 to the power 3000");
  const big = await p.$("#catches .say .big");
  expect(!!big, "huge number: shortened in the catch");
  if (big) {
    const short = await big.textContent();
    await big.click();
    const full = await big.textContent();
    expect(short.length < 20 && full.length > 900, `huge number: tap shows it all (${short.length} -> ${full.length})`);
  }

  // 8. a shared link, then a switch
  await p.evaluate(() => { document.getElementById("code").value = "7 . 10 emit 8 . 10 emit"; });
  await p.click("#share");
  const shared = await p.evaluate(() => location.href);
  const q = await open(browser, "#" + shared.split("#")[1]);
  await idle(q);
  const sharedSays = await says(q);
  expect(JSON.stringify(sharedSays) === JSON.stringify(["7", "8"]), `shared link: runs the shared code (${JSON.stringify(sharedSays)})`);
  expect(await q.$eval("#about", (a) => a.hidden), "shared link: no example's about box");
  expect((await q.$$("#knobs .knob")).length === 0, "shared link: no example's sliders");
  await pick(q, "pearls"); await idle(q);
  await consistent(q, "pearls", "shared link then switch", prints.pearls);

  // 9. switching during the intro: no more Collatz captions or Erdős cards land in the new example
  const r = await open(browser);
  await r.waitForFunction(() => ready && running, null, T);
  await pick(r, "queens"); await idle(r);
  await r.waitForTimeout(9000);
  await consistent(r, "queens", "switch during the intro", prints.queens);
  expect((await r.$$("#catches .catch.find")).length === 0, "switch during the intro: no Erdős cards in Queens");
  expect(!/Collatz|halve|20,000|Erdős/.test(await r.textContent("#caption")) || await r.$eval("#callout", (c) => c.hidden),
    "switch during the intro: no intro captions after leaving");

  // 10. moving a slider during the very first load
  const s = await open(browser, "#hello");
  await s.waitForFunction(() => document.querySelector("#knob0"), null, T);
  await setKnob(s, 0, 10);
  await introOver(s); await idle(s);
  const helloAt10 = await says(s);
  expect(helloAt10.some((l) => l.includes("2 to the power 10")), "slider during load: the run uses the new value");
  await consistent(s, "hello", "slider during load");

  await browser.close();
  if (failures) { console.log(`${failures} of ${checks} checks failed`); process.exit(1); }
  console.log(`ok: ${checks} checks`);
})().catch((e) => { console.log(e); process.exit(1); });
