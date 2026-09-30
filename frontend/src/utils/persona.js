// Two personas share a first name, so the given name alone stops being an
// address. Anyone whose first name is ambiguous is listed here by full id and
// keeps their surname; the first-name map below still covers everyone else.
const EXACT = {
  andrew_nguyen: "Andrew N",
  andrew_kong: "Andrew Kong",
};

const SHORT = {
  phong: "Phong",
  brian: "Brian",
  long: "Long",
  bing: "Bing",
  andrew: "Andrew",
  albert: "Albert",
  alex: "Alex",
};

const ACCENTS = {
  phong_v_cao: "#0d9488",
  brian_gou: "#0284c7",
  andrew_nguyen: "#4d7c0f",
  bing_lin: "#b45309",
  long_nguyen: "#475569",
  albert_liu: "#0176d3",
  alex_mak: "#7c3aed",
  andrew_kong: "#0891b2",
  bruno_raphael: "#be123c",
};

export function shortPersona(personaId) {
  if (!personaId) return "?";
  if (EXACT[personaId]) return EXACT[personaId];
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
  // "Andrew Kong" has to read AK, not AN — take the first letter of each word
  // when there is more than one, and fall back to the first two letters.
  const words = short.trim().split(/\s+/);
  if (words.length > 1) {
    return (words[0][0] + words[words.length - 1][0]).toUpperCase();
  }
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
    alex: "#7c3aed",
  };
  return byKey[key] || "#0284c7";
}
