import { useState } from "react";

/**
 * Compact CSV / Excel download control for table panels.
 */
export default function TableExportMenu({
  label = "Download",
  disabled = false,
  exporting = false,
  onExportCsv,
  onExportExcel,
  hint,
}) {
  const [open, setOpen] = useState(false);
  const busy = disabled || exporting;

  return (
    <div className={`table-export ${open ? "open" : ""}`}>
      <button
        type="button"
        className="ghost-btn table-export-trigger"
        disabled={busy}
        aria-haspopup="menu"
        aria-expanded={open}
        title={hint || "Download table as CSV or Excel"}
        onClick={() => setOpen((v) => !v)}
      >
        {exporting ? "Exporting…" : label}
        <span aria-hidden> ▾</span>
      </button>
      {open ? (
        <>
          <button
            type="button"
            className="table-export-backdrop"
            aria-label="Close export menu"
            onClick={() => setOpen(false)}
          />
          <div className="table-export-menu" role="menu">
            <button
              type="button"
              role="menuitem"
              disabled={busy}
              onClick={async () => {
                setOpen(false);
                await onExportCsv?.();
              }}
            >
              CSV (.csv)
            </button>
            <button
              type="button"
              role="menuitem"
              disabled={busy}
              onClick={async () => {
                setOpen(false);
                await onExportExcel?.();
              }}
            >
              Excel (.xls)
            </button>
          </div>
        </>
      ) : null}
    </div>
  );
}
