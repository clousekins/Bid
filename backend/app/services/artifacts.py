"""Resolve generated resume/cover files inside a job package folder.

Only files that already live in the package folder are returned — callers never
pass a raw filesystem path from the client.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.config import get_settings
from app.services.submission_capture import list_captures

KINDS = ("resume_pdf", "resume_html", "cover_pdf", "cover_txt", "submission_png")

_KIND_MEDIA = {
    "resume_pdf": "application/pdf",
    "cover_pdf": "application/pdf",
    "resume_html": "text/html; charset=utf-8",
    "cover_txt": "text/plain; charset=utf-8",
    "submission_png": "image/png",
}


@dataclass(frozen=True)
class ArtifactFile:
    kind: str
    path: Path
    media_type: str

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def size(self) -> int:
        try:
            return self.path.stat().st_size
        except OSError:
            return 0

    @property
    def mtime(self) -> float:
        try:
            return self.path.stat().st_mtime
        except OSError:
            return 0.0


def _is_cover(name: str) -> bool:
    n = name.lower()
    return "cover" in n or n.startswith("cover_letter")


def _pick(paths: list[Path], *, cover: bool, suffixes: tuple[str, ...]) -> Path | None:
    matches = [
        p
        for p in paths
        if p.suffix.lower() in suffixes and _is_cover(p.name) == cover and p.is_file()
    ]
    if not matches:
        return None
    matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return matches[0]


def resolve_artifacts(folder: str | Path | None) -> dict[str, ArtifactFile]:
    if not folder:
        return {}
    root = Path(folder)
    if not root.is_dir():
        return {}
    files = [p for p in root.iterdir() if p.is_file()]
    out: dict[str, ArtifactFile] = {}
    mapping = {
        "resume_pdf": _pick(files, cover=False, suffixes=(".pdf",)),
        "resume_html": _pick(files, cover=False, suffixes=(".html", ".htm")),
        "cover_pdf": _pick(files, cover=True, suffixes=(".pdf",)),
        "cover_txt": _pick(files, cover=True, suffixes=(".txt",)),
        # Proof of what was on screen when we submitted. Newest wins; the rest
        # stay in the folder and are listed by list_captures().
        "submission_png": next(iter(list_captures(root)), None),
    }
    for kind, path in mapping.items():
        if path is None:
            continue
        out[kind] = ArtifactFile(kind=kind, path=path, media_type=_KIND_MEDIA[kind])
    return out


def folder_is_allowed(folder: str | Path | None) -> bool:
    """Package folders must live under PERSONA_OUTPUT_ROOT."""
    if not folder:
        return False
    try:
        Path(folder).resolve().relative_to(get_settings().output_root.resolve())
        return True
    except (OSError, ValueError):
        return False


def resolve_kind(folder: str | Path | None, kind: str) -> ArtifactFile:
    if kind not in KINDS:
        raise KeyError(kind)
    if not folder or not folder_is_allowed(folder):
        raise FileNotFoundError(kind)
    found = resolve_artifacts(folder).get(kind)
    if not found:
        raise FileNotFoundError(kind)
    # Stay inside the package folder even if a symlink is present.
    root = Path(folder).resolve()
    target = found.path.resolve()
    if root not in target.parents and target != root:
        raise FileNotFoundError(kind)
    return found
