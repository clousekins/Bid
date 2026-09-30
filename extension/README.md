# Poong Job Clipper

A panel that opens **on the page**. Click the toolbar icon and it appears in the
top-right corner of whatever you are looking at; click again to close it. It
stays put while you scroll, click and read — unlike a popup, which the browser
destroys the moment it loses focus.

Two halves, which is what the panel shows.

**This page** — read the job posting in the tab into editable fields, then add
it to Poong-Apply. The fields are editable because the reading is a guess about
someone else's HTML, and a wrong company name is easier to fix before saving
than after.

**Jobright JSON** — paste a `/swan/recommend/search` reply, press Add, repeat.
The jobs accumulate and duplicates are dropped. Download CSV writes the lot to
one file and shows where it landed.

The panel is drag-able by its title bar, and Esc closes it. It lives in the page
it was opened on, so navigating or reloading closes it too — click the icon
again on the new page.

## Installing

1. Open `chrome://extensions` (or `edge://extensions`).
2. Turn on **Developer mode**.
3. **Load unpacked** → pick this `extension/` folder.
4. Open **Details → Extension options** and set:
   - **Backend URL** — `http://127.0.0.1:8000` unless you moved it.
   - **Access token** — `ACCESS_TOKEN` from the repo's `.env`. Leave blank if
     that setting is empty.
   - **Persona id** — optional; blank uses whichever persona is active.

   Press **Test connection**. It should answer with the job count.

The backend has to be running for the first half — the same
`uvicorn app.main:app` the rest of the project uses. The JSON-to-CSV half needs
nothing but the browser.

## Why there is a service worker

The panel is a content script, and a content script may do neither of the two
things this extension exists for:

- **Its fetches carry the page's origin.** A call to `127.0.0.1:8000` from
  inside `greenhouse.io` is a cross-origin request that the backend's CORS
  policy decides on, and `host_permissions` does not exempt it.
- **`chrome.downloads` is not available to it** at all.

So both go through `background.js`, which the panel talks to by message. The
worker also writes the CSV as a `data:` URL rather than a blob one, because
`URL.createObjectURL` does not exist in an MV3 service worker — and a blob
minted in the page's context is not something the worker could read anyway.

## Reading a page

Three readings are tried, best first:

| Where the data comes from | When |
| --- | --- |
| Jobright's `__NEXT_DATA__` | on `jobright.ai/jobs/info/…`, **and only when its job id matches the address bar** |
| Jobright's rendered page | on Jobright when that record is stale — the heading and title are not |
| `schema.org/JobPosting` markup | Greenhouse, Lever, Ashby, Workday and most ATSes publish it |
| The visible page | anything else — the fields are a guess, and the panel says so |

The URL comes from the address bar unless the record named a better one; it is
the only field that is always right. The panel hides itself while reading, so
the last reading does not scrape the panel's own labels.

**Why the id has to match.** Jobright is a single-page app. Clicking from one
job to the next changes the address bar without re-rendering `__NEXT_DATA__`,
which keeps whatever job the tab first loaded. Reading it blindly meant every
page reported the same company — a confident, wrong answer, which is worse than
no answer. So an embedded record whose `jobId` is not the one in the URL is
discarded, and the panel reads the page as rendered instead. For the same
reason a list page contributes nothing: `defaultData` holds thirty jobs there,
and none of them is "this page".

**Add to Poong** asks the backend whether that URL is already on file before
posting. The backend deduplicates by URL on its own, but answers a duplicate
with the same `200` and the same job body as a fresh insert — so asking first is
the only way to tell you which of the two happened.

## Collecting Jobright JSON

Sign in to jobright.ai, open a search, and copy the response to
`POST /swan/recommend/search` out of DevTools → Network. The whole envelope is
fine; so is the inner `result`, a bare `jobList`, or a single entry — whichever
node you happened to right-click.

Jobs are identified by Jobright's `jobId`, so pasting overlapping searches is
safe: the second copy of a posting is counted and dropped, not stored twice. The
collected list lives in `chrome.storage.local`, so it survives closing the panel
and closing the browser — **Clear** is the only thing that empties it.

**The score slider** sets a floor on Jobright's own match score, which rides on
each entry as `displayScore` — roughly 40–98 in practice, so most of the 1–100
range is headroom. Anything below the floor is dropped as it is added, and the
count says how many. A job with *no* score is kept and counted separately:
dropping it would be judging it by a number it never had.

The floor applies at the moment of adding. Raising it does not remove jobs
already collected — Clear and paste again for that.

**Push jobs** sends everything collected to the backend, one at a time, and
reports what it did:

- *added* — new to the backend.
- *already on file* — it was there before. `POST /api/jobs` deduplicates by
  URL, but answers a duplicate with the same `200` and the same job body as a
  fresh insert. So the push reads the highest sequence number first and
  compares afterwards: a row that already existed keeps its old, lower number.
  That distinction costs no extra requests.
- *refused* — a gate turned it away, usually a company already applied to.

Pushed rows are marked, so a second press sends only what is new rather than
asking the backend about everything again. The mark survives with the basket;
Clear forgets it, and a re-push of the same jobs is then reported honestly as
*already on file*.

The CSV columns are the ones `manual_ingest.FIELD_ALIASES` recognises, so the
file can be handed straight back to `POST /api/jobs/import`:

```
company, title, url, apply_url, location, posted_at, seniority,
employment_type, remote, salary, score, source, external_id, description
```

It is written with CRLF endings and a UTF-8 BOM, because Excel renders the
accented company names in this data as mojibake without one.

## Writing resumes with ChatGPT

Open chatgpt.com, signed in, and click the extension icon there. Instead of
the clipper you get the **ChatGPT resumes** panel, and on chatgpt.com you get
nothing else.

1. Pick who to write as, and tick jobs. The list is Poong's "needs a resume"
   for that persona: a usable job description and no resume yet, or one that
   failed. Switch *Show* to see every job with a JD, or type to search.
2. Press **Generate**. The jobs run one at a time, each in a new chat in this
   tab. For each, the backend hands over a prompt (the persona prompt, the
   candidate profile and the job description), the panel pastes it and presses
   send, waits for ChatGPT to finish, and hands the answer back. Then the
   screening questions, in the same chat. The backend may ask for more, such as
   an ATS retry, and the panel follows until the job says it is done.
3. The backend writes the same files an API-generated resume gets: the HTML and
   PDF resume, the cover letter, `answers.json`, the ATS score, and the job
   folder. It also writes `chatgpt_raw.txt`, with what was sent and what came
   back. Each job's pill shows its progress, then done with its ATS score, or
   failed. Hover over it to see why.

Keep the tab in front while it works: Chrome slows hidden tabs down. **Stop**
ends the run after the current step.

If the panel cannot find ChatGPT's send button, it asks you to click it once.
Your click sends that prompt, and the panel remembers the button and presses it
itself from then on.

The prompt goes in as one paste. Typing it froze the page, since a prompt runs
to hundreds of lines. ChatGPT may turn the paste into a "Pasted text"
attachment, which works the same. The answer comes out through the message's
own Copy button, with the clipboard intercepted for that one click, so your
clipboard is never touched.

The backend's `config/runtime.yml` holds `chatgpt_timeout_seconds`, the longest
one reply may take (360 by default). It is a ceiling, not a delay.

## Layout

```
manifest.json    MV3. No default_popup — that is what makes the icon click fire
                 chrome.action.onClicked, which toggles the panel.
background.js    Service worker: the backend calls and the file writing.
content.js       The only classic script. Toggles the panel, then dynamically
                 imports lib/panel.js (lib/gpt-panel.js on chatgpt.com) — a
                 content script cannot be declared as a
                 module, but it may import one.
lib/shell.js     The frame both panels share: shadow root, dragging, closing.
lib/panel.js     The clipper panel and its cards.
lib/gpt-panel.js The ChatGPT resumes panel (chatgpt.com only).
lib/gpt-page.js  What that panel does on the ChatGPT page, run in its own world.
ui.css           Shared by the panel's shadow root and the options page.
options.html/.js Backend URL, token, persona.
lib/api.js       The backend calls (worker and options page only).
lib/settings.js  What is stored, and why it is local rather than synced.
lib/extract.js   Reading a job out of the page.
lib/jobright.js  Search payload → rows.
lib/csv.js       Rows → CSV, in the importer's column names.
lib/basket.js    The collected rows, in chrome.storage.local.
```

Everything the panel loads is listed in `web_accessible_resources`, since a
dynamic import from a content script fetches by extension URL. `lib/api.js` is
deliberately **not** in the panel's import graph — if it were, its fetches would
be the CORS-blocked ones described above.

The panel renders inside a shadow root. That is not tidiness: it opens on pages
whose stylesheets say things like `div { position: static !important }`, and
without the boundary it inherits all of it. `all: initial` on `:host` handles the
one thing a shadow boundary does not stop, which is inheritance — and the
properties that decide where the panel sits are set inline with `!important`
from `panel.js`, because even a `:host` rule loses to the page for those.

Nothing is collected or sent anywhere except the backend URL you configure. The
access token lives in `chrome.storage.local` — on this machine, not synced to a
Google account.
