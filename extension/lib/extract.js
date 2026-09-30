// What the "Read this page" button runs.
//
// The panel is itself in the page, so this just reads the DOM around it — a
// content script's world is isolated from the page's scripts but shares its
// document. Every helper stays inside the one function: it costs nothing, and
// it keeps this callable from a detached context (an injected script, a worker
// with a parsed document) if the panel ever moves again.
//
// Three readings, best first. Jobright's own pages carry the whole record in
// __NEXT_DATA__, so on those there is nothing to guess at. Failing that, most
// applicant tracking systems — Greenhouse, Lever, Ashby, Workday — publish a
// schema.org JobPosting, which is a description written for machines. Only when
// neither is there does this fall back to reading the visible page, and that
// last reading is the one whose output a person should check before saving.

export function extractJobFromPage() {
  const MAX_DESCRIPTION = 20000;

  const clean = (value) =>
    String(value == null ? "" : value)
      .replace(/ /g, " ")
      .replace(/[ \t]+/g, " ")
      .replace(/\n{3,}/g, "\n\n")
      .trim();

  const fromHtml = (html) => {
    const holder = document.createElement("div");
    holder.innerHTML = String(html || "");
    return clean(holder.textContent);
  };

  const meta = (selector) => {
    const node = document.querySelector(selector);
    return node ? clean(node.getAttribute("content")) : "";
  };

  const firstText = (selectors) => {
    for (const selector of selectors) {
      const node = document.querySelector(selector);
      const text = node ? clean(node.textContent) : "";
      if (text) return text;
    }
    return "";
  };

  const placeName = (place) => {
    if (!place) return "";
    if (typeof place === "string") return clean(place);
    if (Array.isArray(place)) return place.map(placeName).filter(Boolean).join("; ");
    const address = place.address || place;
    if (typeof address === "string") return clean(address);
    const parts = [
      address.addressLocality,
      address.addressRegion,
      address.addressCountry && (address.addressCountry.name || address.addressCountry),
    ];
    return clean(parts.filter(Boolean).join(", "));
  };

  // The job this page is about, according to the address bar. On Jobright that
  // is the only thing on the page guaranteed to be current — see below.
  const jobrightIdFromUrl = () => {
    const match = /\/jobs\/info\/([0-9a-z]{12,})/i.exec(location.pathname);
    return match ? match[1] : "";
  };

  // --- 1. Jobright, which embeds the record it rendered from ----------------
  const jobrightRecord = () => {
    const wanted = jobrightIdFromUrl();
    // Only a job page has an embedded job worth reading. On a list page
    // `defaultData` holds thirty of them and the first one is not "this page".
    if (!wanted) return null;

    const tag = document.getElementById("__NEXT_DATA__");
    if (!tag) return null;
    let props;
    try {
      props = JSON.parse(tag.textContent).props.pageProps;
    } catch {
      return null;
    }
    const candidates = [props && props.dataSource].concat(
      Array.isArray(props && props.defaultData) ? props.defaultData : []
    );
    // Jobright is a single-page app: clicking through the job list changes the
    // address bar without re-rendering __NEXT_DATA__, which stays as whichever
    // job the tab first loaded. A record whose id is not the one in the URL
    // describes a different job, and filling the form from it is worse than not
    // reading at all — it looks like a successful read of the wrong posting.
    const entry = candidates.find(
      (item) => item && item.jobResult && String(item.jobResult.jobId) === wanted
    );
    if (!entry) return null;

    const job = entry.jobResult;
    const company = entry.companyResult || {};
    const pieces = [job.jobSummary, job.jdResponsibilitySummary];
    for (const key of ["coreResponsibilities", "skillSummaries", "qualifications"]) {
      const value = job[key];
      if (Array.isArray(value)) pieces.push(value.join("\n"));
      else if (value && typeof value === "object" && Array.isArray(value.mustHave)) {
        pieces.push(value.mustHave.join("\n"));
      } else if (value) pieces.push(String(value));
    }
    if (company.companyDesc) pieces.push(String(company.companyDesc));
    if (job.salaryDesc) pieces.push(`Compensation: ${job.salaryDesc}`);

    const workModel = clean(job.workModel);
    let location = clean(job.jobLocation);
    if (workModel && location && !location.toLowerCase().includes(workModel.toLowerCase())) {
      location = `${location} (${workModel})`;
    }

    return {
      company: clean(company.companyName),
      title: clean(job.jobTitle || job.jobNlpTitle),
      url: job.jobId ? `https://jobright.ai/jobs/info/${job.jobId}` : "",
      apply_url: clean(job.applyLink || job.originalUrl),
      location,
      description: clean(pieces.filter(Boolean).join("\n\n")).slice(0, MAX_DESCRIPTION),
      external_id: clean(job.jobId),
      source: "Jobright",
      readAs: "Jobright page data",
    };
  };

  // --- 1b. Jobright, read from the page as rendered -------------------------
  //
  // Where the embedded record is stale, the visible page is not: the heading
  // and the document title are re-rendered on every client-side navigation.
  // Jobright publishes no JobPosting markup, so without this a stale record
  // would fall all the way through to the generic reader, which guesses the
  // company by splitting the title and keeps "| Jobright.ai" as part of it.
  const jobrightDomRecord = () => {
    if (!/(^|\.)jobright\.ai$/i.test(location.hostname)) return null;
    const id = jobrightIdFromUrl();
    if (!id) return null;

    // "Data Engineer Specialist @ Amerit Fleet Solutions | Jobright.ai"
    const titleTag = clean(document.title).replace(/\s*\|\s*Jobright(\.ai)?\s*$/i, "");
    const split = /^(.*?)\s+@\s+(.*)$/.exec(titleTag);

    // The company also sits in the first heading under the logo, as
    // "Amerit Fleet Solutions · 22 minutes ago".
    const subhead = firstText(["h2"]).split(/\s*[·•]\s*/)[0];

    const title = firstText(["h1"]) || (split ? clean(split[1]) : titleTag);
    const company = (split ? clean(split[2]) : "") || clean(subhead);
    if (!title || !company) return null;

    const main = document.querySelector("main, article, [role=main]") || document.body;
    return {
      company,
      title,
      url: `https://jobright.ai/jobs/info/${id}`,
      apply_url: "",
      location: "",
      description: clean(main.innerText).slice(0, MAX_DESCRIPTION),
      external_id: id,
      source: "Jobright",
      readAs: "Jobright page (rendered — check the fields)",
    };
  };

  // --- 2. schema.org JobPosting --------------------------------------------
  const jsonLdRecord = () => {
    const blocks = document.querySelectorAll('script[type="application/ld+json"]');
    for (const block of blocks) {
      let parsed;
      try {
        parsed = JSON.parse(block.textContent);
      } catch {
        continue;
      }
      const candidates = Array.isArray(parsed)
        ? parsed
        : Array.isArray(parsed["@graph"])
          ? parsed["@graph"]
          : [parsed];
      for (const node of candidates) {
        if (!node || node["@type"] !== "JobPosting") continue;
        const org = node.hiringOrganization || {};
        let where = placeName(node.jobLocation) || placeName(node.applicantLocationRequirements);
        if (node.jobLocationType === "TELECOMMUTE") {
          where = where ? `${where} (Remote)` : "Remote";
        }
        return {
          company: clean(typeof org === "string" ? org : org.name),
          title: clean(node.title),
          url: clean(node.url),
          apply_url: clean(node.applicationContact && node.applicationContact.url),
          location: where,
          description: fromHtml(node.description).slice(0, MAX_DESCRIPTION),
          external_id: clean(node.identifier && (node.identifier.value || node.identifier)),
          source: "",
          readAs: "JobPosting markup",
        };
      }
    }
    return null;
  };

  // --- 2b. Greenhouse job boards -------------------------------------------
  //
  // job-boards.greenhouse.io/<company>/jobs/<id> — the employer is the first
  // path segment, and on these pages it is the one thing always available.
  // They publish no JobPosting markup at all and leave og:site_name empty, so
  // the generic reading below ran out of places to look and fell back to the
  // hostname, filing every one of them under "job-boards.greenhouse.io".
  //
  // The same board also serves an embedded form, .../embed/job_app?for=<company>
  // — that is the link Jobright hands out for Greenhouse employers, so it is
  // worth reading too.
  const greenhouseFromUrl = () => {
    if (!/(^|\.)greenhouse\.io$/i.test(location.hostname)) return null;

    const params = new URLSearchParams(location.search);
    const embedded = params.get("for");
    if (embedded) {
      return {
        company: clean(embedded),
        id: clean(params.get("token")),
        url: location.href,
      };
    }

    const match = /^\/([^/]+)\/jobs\/([0-9]+)/.exec(location.pathname);
    if (!match || match[1].toLowerCase() === "embed") return null;
    return {
      company: clean(match[1]),
      id: clean(match[2]),
      url: `https://${location.hostname}/${match[1]}/jobs/${match[2]}`,
    };
  };

  const greenhouseRecord = () => {
    const board = greenhouseFromUrl();
    if (!board || !board.company) return null;
    // The heading is the role. og:title carries it too, and og:description is
    // where these boards put the location.
    const title = firstText(["h1"]) || meta('meta[property="og:title"]');
    if (!title) return null;

    const main = document.querySelector("main, article, [role=main]") || document.body;
    return {
      company: board.company,
      title,
      url: board.url || location.href,
      apply_url: "",
      location: meta('meta[property="og:description"]'),
      description: clean(main.innerText).slice(0, MAX_DESCRIPTION),
      external_id: board.id,
      source: "Greenhouse",
      readAs: "Greenhouse board (employer from the URL)",
    };
  };

  // --- 3. The page as a reader sees it -------------------------------------
  const visibleRecord = () => {
    // og:title is usually "Senior Data Engineer at Acme" or "… - Acme"; the
    // separator is worth splitting on, but only to fill a company we do not
    // otherwise have.
    const heading = firstText(["h1", '[data-testid="job-title"]', ".job-title"]);
    const ogTitle = meta('meta[property="og:title"]');
    let title = heading || ogTitle || clean(document.title);
    let company =
      meta('meta[property="og:site_name"]') ||
      firstText(['[data-testid="company-name"]', ".company-name", '[itemprop="hiringOrganization"]']);

    // A tab title is usually "<role> at <company> | <site>". Drop the site
    // first, or it ends up glued to the company name.
    const bare = clean(ogTitle || document.title || "")
      .replace(/\s*[|·–—]\s*[^|·–—]{0,40}$/, (tail) =>
        /\s(at|@)\s/.test(tail) ? tail : ""
      );
    const split = /^(.*?)\s+(?:at|@|[-–|·])\s+(.*)$/.exec(bare);
    if (split) {
      if (!company) company = clean(split[2]);
      if (!heading) title = clean(split[1]);
    }
    if (!company) company = location.hostname.replace(/^www\./, "");

    const main = document.querySelector("main, article, [role=main]") || document.body;
    return {
      company: clean(company),
      title: clean(title),
      url: "",
      apply_url: "",
      location: firstText(['[data-testid="location"]', ".location", '[itemprop="jobLocation"]']),
      description: clean(main.innerText).slice(0, MAX_DESCRIPTION),
      external_id: "",
      source: "",
      readAs: "page text (check these fields)",
    };
  };

  const found =
    jobrightRecord() ||
    jobrightDomRecord() ||
    jsonLdRecord() ||
    greenhouseRecord() ||
    visibleRecord();

  // A board's hostname is not an employer. Where the URL names one — and on
  // Greenhouse it always does — prefer it over the fallback that files the job
  // under the site it was posted on. This runs whichever reading won, so a
  // page that gains JobPosting markup later still gets a company either way.
  const board = greenhouseFromUrl();
  if (board && board.company) {
    if (!found.company || /greenhouse\.io$/i.test(found.company)) {
      found.company = board.company;
    }
    found.external_id = found.external_id || board.id;
  }

  // The address bar is the job's URL unless the record named a better one, and
  // it is the only field that is always right — everything else is a reading.
  found.url = found.url || location.href;
  found.source = found.source || location.hostname.replace(/^www\./, "");
  if (!found.external_id) {
    const tail = location.pathname.split("/").filter(Boolean).pop() || "";
    found.external_id = /^[0-9a-zA-Z_-]{4,}$/.test(tail) ? tail : "";
  }
  found.pageUrl = location.href;
  return found;
}
