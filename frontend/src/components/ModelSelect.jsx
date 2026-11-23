import { useEffect, useState } from "react";
import { client, getTailorModel, setTailorModelChoice } from "../api.js";

/**
 * Which OpenAI model the API tailor path uses.
 *
 * The list is the chat-capable models on the account (so the ids are real, not
 * guessed). The choice is kept in this browser and sent with each generate
 * request, so two people picking different models each get their own. A
 * browser that has never picked starts from the server's saved default. Only
 * the API-key path uses it; the ChatGPT/Claude extension path drives the model
 * in the browser.
 */
export default function ModelSelect() {
  const [model, setModel] = useState("");
  const [models, setModels] = useState([]);
  const [error, setError] = useState("");
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    client
      .tailorModel()
      .then((d) => {
        if (cancelled) return;
        const mine = getTailorModel();
        if (!mine && d.model) setTailorModelChoice(d.model);
        setModel(mine || d.model || "");
        setModels(Array.isArray(d.models) ? d.models : []);
        setLoaded(true);
      })
      .catch((e) => {
        if (cancelled) return;
        setModel(getTailorModel() || "");
        setError(String(e.message || e));
        setLoaded(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const choose = (next) => {
    setTailorModelChoice(next);
    setModel(next);
  };

  if (!loaded) return null;

  // The current model may not be in the fetched list (no key, or a pinned id).
  const options = models.includes(model) || !model ? models : [model, ...models];

  return (
    <div className="model-select">
      <label htmlFor="tailor-model" className="model-select-title">
        API model
      </label>
      {options.length ? (
        <select id="tailor-model" value={model} onChange={(e) => choose(e.target.value)}>
          {options.map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </select>
      ) : (
        <span className="muted">
          No models found — set OPENAI_API_KEY, or the account list could not load.
        </span>
      )}
      <span className="muted model-select-hint">
        Saved in this browser only. Used by the API-key Generate path (not the extension).
      </span>
      {error ? <p className="error-text">{error}</p> : null}
    </div>
  );
}
