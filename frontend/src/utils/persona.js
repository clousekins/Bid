const SHORT = {
  phong: "Phong",
  brian: "Brian",
  long: "Long",
  bing: "Bing",
  andrew: "Andrew",
  albert: "Albert",
};

const ACCENTS = {
  phong_v_cao: "#0d9488",
  brian_gou: "#0284c7",
  andrew_nguyen: "#4d7c0f",
  bing_lin: "#b45309",
  long_nguyen: "#475569",
  albert_liu: "#0176d3",
};

export function shortPersona(personaId) {
  if (!personaId) return "?";
  const key = personaId.split("_")[0];
  if (SHORT[key]) return SHORT[key];
  return key.charAt(0).toUpperCase() + key.slice(1);
}

export function personaLabel(personaId) {
  if (!personaId) return "—";
  return personaId
    .split("_")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

export function personaInitials(personaId) {
  const short = shortPersona(personaId);
  return short.slice(0, 2).toUpperCase();
}

export function personaAccent(personaId) {
  if (ACCENTS[personaId]) return ACCENTS[personaId];
  const key = (personaId || "").split("_")[0];
  const byKey = {
    phong: "#0d9488",
    brian: "#0284c7",
    andrew: "#4d7c0f",
    bing: "#b45309",
    long: "#475569",
    albert: "#0176d3",
  };
  return byKey[key] || "#0284c7";
}
