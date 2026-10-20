import { useEffect, useState } from "react";
import { client } from "../api.js";

/**
 * Which OpenAI model the API tailor path uses.
 *
 * The list is the chat-capable models on the account (so the ids are real, not
 * guessed); the choice is a runtime setting, so it persists across restarts and
 * takes effect on the next Generate — no .env edit or restart. Only the API-key
 * path uses it; the ChatGPT/Claude extension path drives the model in the browser.
 */
export default function ModelSelect() {
  const [model, setModel] = useState("");
  const [models, setModels] = useState([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    client
      .tailorModel()
      .then((d) => {
        if (cancelled) return;
        setModel(d.model || "");
        setModels(Array.isArray(d.models) ? d.models : []);
        setLoaded(true);
      })
      .catch((e) => {
        if (cancelled) return;
        setError(String(e.message || e));
        setLoaded(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const persist = async (next) => {
    const prev = model;
    setModel(next);
    setSaving(true);
    setError("");
    try {
      const saved = await client.setTailorModel(next);
      setModel(saved.model || next);
    } catch (e) {
      setModel(prev);
      setError(String(e.message || e));
    } finally {
      setSaving(false);
    }
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
        <select
          id="tailor-model"
          value={model}
          disabled={saving}
          onChange={(e) => persist(e.target.value)}
        >
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
        Used by the API-key Generate path (not the extension).
      </span>
      {error ? <p className="error-text">{error}</p> : null}
    </div>
  );
}
