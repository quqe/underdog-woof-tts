// SPDX-FileCopyrightText: 2026 Ilia Okhrimenko
// SPDX-License-Identifier: MIT
//
// Optional headless check for web/index.html. Needs jsdom:
//   npm install --no-save jsdom && node tests/ui.test.js
// It stubs fetch and the media element, then walks submit -> generating -> result
// and the error path. No browser and no network required.

// Headless sanity check for web/index.html: runs the page script in jsdom with stubbed
// network/media APIs and verifies submit -> generating -> result and the error path.
const fs = require("fs");
const path = require("path");
const { JSDOM, VirtualConsole } = require("jsdom");

const html = fs.readFileSync(path.join(__dirname, "..", "web", "index.html"), "utf8");
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];

const requests = [];
const audioHeaders = {
  "X-Voice": "juno",
  "X-Rate": "1.3",
  "X-Audio-Seconds": "9.23",
  "X-Generation-Seconds": "4.10",
};

const virtualConsole = new VirtualConsole();
virtualConsole.on("jsdomError", (e) => console.error("page error:", e.message));

const dom = new JSDOM(html, {
  url: "http://localhost:3005/",
  runScripts: "outside-only",
  pretendToBeVisual: true,
  virtualConsole,
});

const { window } = dom;

window.URL.createObjectURL = () => "blob:fake";
window.URL.revokeObjectURL = () => {};
window.HTMLMediaElement.prototype.play = () => Promise.resolve();
window.HTMLMediaElement.prototype.pause = () => {};

function stubFetch(handler) {
  window.fetch = (url) => {
    requests.push(url);
    return handler(url);
  };
}

stubFetch((url) => {
  if (url === "/health") {
    return Promise.resolve({
      ok: true, status: 200, headers: { get: () => null },
      json: async () => ({ status: "ok", max_chars: 1500 }),
    });
  }
  // delay so the transient "generating" state is observable
  return new Promise((resolve) => setTimeout(() => resolve({
    ok: true, status: 200,
    blob: async () => new window.Blob(["RIFF....WAVE"], { type: "audio/wav" }),
    headers: { get: (name) => audioHeaders[name] || null },
    json: async () => ({}),
  }), 75));
});

window.eval(script);

const tick = () => new Promise((r) => setTimeout(r, 30));
const failures = [];
const expect = (cond, msg) => { if (!cond) failures.push(msg); };

(async () => {
  const $ = (id) => window.document.getElementById(id);

  await tick();
  expect(requests.includes("/health"), "health request was not made");
  expect($("counter").textContent === $("text").value.length + " / 1500",
    `counter after health = "${$("counter").textContent}"`);
  expect($("result").classList.contains("hidden"), "result should start hidden");
  expect($("cancel").classList.contains("hidden"), "cancel should start hidden");
  expect($("rate").value === "1.3", `default rate = ${$("rate").value}`);

  $("form").dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true }));
  await tick();
  expect($("speak").disabled === true, "speak button should be disabled while generating");
  expect($("speak").textContent.includes("Generating"), `button text = "${$("speak").textContent}"`);
  expect($("status").textContent.includes("Generating"), `status = "${$("status").textContent}"`);
  expect(!$("cancel").classList.contains("hidden"), "cancel should be visible while generating");

  await new Promise((r) => setTimeout(r, 150));
  const ttsUrl = requests.find((u) => u.startsWith("/tts?"));
  expect(!!ttsUrl, "no /tts request");
  expect(/text=Hey(\+|%20)guys/.test(ttsUrl || ""), `url text param: ${ttsUrl}`);
  expect((ttsUrl || "").includes("voice=juno"), "voice param missing");
  expect((ttsUrl || "").includes("rate=1.3"), "rate param missing");
  expect(!$("result").classList.contains("hidden"), "result should be visible");
  expect($("meta").textContent === "Juno \u00b7 1.3\u00d7 \u00b7 9.23s of audio \u00b7 generated in 4.10s",
    `meta = "${$("meta").textContent}"`);
  expect($("audio").src === "blob:fake", `audio src = ${$("audio").src}`);
  expect($("speak").disabled === false, "speak should be re-enabled");
  expect($("status").textContent === "", `status should be cleared, got "${$("status").textContent}"`);
  expect(window.document.title === "Underdog Woof TTS", `title = ${window.document.title}`);

  stubFetch(() => Promise.resolve({
    ok: false, status: 400, headers: { get: () => null },
    json: async () => ({ error: "text too long: 2100 chars, limit is 2000" }),
  }));
  $("form").dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true }));
  await tick(); await tick();
  expect(!$("error").classList.contains("hidden"), "error box should be visible");
  expect($("error").textContent.includes("text too long"), `error text = "${$("error").textContent}"`);

  if (failures.length) {
    console.error("FAILURES:\n - " + failures.join("\n - "));
    process.exit(1);
  }
  console.log("ui checks passed");
})();
