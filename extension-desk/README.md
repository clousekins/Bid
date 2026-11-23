# Poong Job Desk

A Chrome side panel for working through Poong's jobs while applying. The panel stays open when you switch tabs.

## Install

1. Open `chrome://extensions` and turn on **Developer mode**.
2. Click **Load unpacked** and pick this `extension-desk` folder.
3. Click the toolbar icon to open the side panel.
4. Click the gear icon and enter the backend URL (default `http://127.0.0.1:8000`) and the `ACCESS_TOKEN` from `.env`. Then click **Save & test**.

## Use

- **Persona**: the select at the top. Each persona has its own job list.
- **Search**: type a company or title. Type a number (`123` or `#123`) to jump to that job number. If only one job matches, Enter opens it.
- **List**: the list on top is small. Drag its bottom edge to make it taller. It shows 50 jobs per page. Use the pager under it to move between pages: first, previous, next and last, or type a page number and press Enter.
- **Resume and Cover chips**: drag either chip onto a file upload box on the page, or onto the desktop or an Explorer window. Click a chip to open the file in a new tab. A file is used in this order:
  - resume: the PDF, then the HTML
  - cover letter: the PDF, then the TXT
- **JD + Resume and Interview prompt**: builds the same text as Poong's Continue-apply window, copies it to the clipboard and shows it in a box.
- **Other buttons**: the same actions as Poong's job detail. They cover editing the job fields, Generate, Fetch JD, Mark applied, Skip, the interview, rejected, federal and disabled toggles, Open folder, Block company, Delete job and the outcome.

## Files

The panel prefetches a job's files when you hover its row or open it. A drag has to hand over the file at the moment it starts, so the file must already be loaded.

Chrome does not pass a real file from the side panel into a web page. So `drop.js` runs in every page and frame. While a Resume or Cover chip is dragged, it outlines the upload box under the pointer. On the drop, it fetches the file through the extension and attaches it in one of two ways:

- If the box has a nearby `<input type=file>`, the file is set on that input, the same as picking it in the file dialog.
- Otherwise, the drop is replayed on the box with the file on it.

A green "attached" note appears in the corner when it works.

Tabs that were already open before the extension was installed or reloaded must be reloaded once to get `drop.js`.
