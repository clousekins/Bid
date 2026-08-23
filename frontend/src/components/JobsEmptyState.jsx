import { EMPTY_PIPELINE_IMAGE } from "../data/heroSlides.js";

export default function JobsEmptyState({ onFetch }) {
  return (
    <div className="jobs-empty-state">
      <div className="jobs-empty-visual">
        <img src={EMPTY_PIPELINE_IMAGE} alt="" className="jobs-empty-img" loading="lazy" />
        <div className="jobs-empty-scrim" />
      </div>
      <div className="jobs-empty-copy">
        <h3>Your pipeline is empty</h3>
        <p className="muted">
          Start by fetching remote US roles from ATS boards and public feeds. Pick domains like AI,
          Data Engineering, or Full Stack to stay focused.
        </p>
        {onFetch ? (
          <button type="button" className="primary" onClick={onFetch}>
            Fetch new jobs
          </button>
        ) : null}
      </div>
    </div>
  );
}
