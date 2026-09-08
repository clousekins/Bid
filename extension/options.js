import { ping } from "./lib/api.js";
import { loadSettings, saveSettings } from "./lib/settings.js";

const $ = (id) => document.getElementById(id);

function say(text, tone = "") {
  const node = $("msg");
  node.textContent = text;
  node.className = `message ${tone}`.trim();
  node.hidden = !text;
}

async function fill() {
  const settings = await loadSettings();
  $("baseUrl").value = settings.baseUrl;
  $("accessToken").value = settings.accessToken;
  $("personaId").value = settings.personaId;
}

$("btnSave").addEventListener("click", async () => {
  await saveSettings({
    baseUrl: $("baseUrl").value,
    accessToken: $("accessToken").value,
    personaId: $("personaId").value,
  });
  await fill();
  say("Saved.", "ok");
});

$("btnTest").addEventListener("click", async () => {
  // Saved first: the test reads the stored settings, so testing what is on
  // screen means writing it down before asking.
  await saveSettings({
    baseUrl: $("baseUrl").value,
    accessToken: $("accessToken").value,
    personaId: $("personaId").value,
  });
  say("Testing…");
  try {
    const { total } = await ping();
    say(`Connected — ${total} jobs on file.`, "ok");
  } catch (err) {
    say(err.message, "bad");
  }
});

fill();
