"""The router: pick engines per file type and per page kind, run them in order, and move
to the next engine when the output looks bad. Every attempt is written to a decision log.

The big picture for a PDF:
    triage every page -> group pages by kind -> for each group run its engine chain
    -> accept an engine's page if text_quality >= threshold, else try the next engine
    -> if nothing passes, keep the best attempt and warn.

Files without pages (docx, csv ...) use the same loop with the whole file as one "page 0".
"""

from __future__ import annotations

import logging
import threading
import time
from collections import Counter
from dataclasses import dataclass, field

from doctordoc.config import Config
from doctordoc.detect import IMAGE_TYPES, FileInfo
from doctordoc.engines import build_engines
from doctordoc.engines.base import Engine
from doctordoc.merge import merge_missing_blocks
from doctordoc.models import Decision, EngineRun, Page, PageKind
from doctordoc.quality import quality_report
from doctordoc.triage import triage_pdf

log = logging.getLogger("doctordoc.router")

MODEL_LOAD_ALLOWANCE = 60.0  # extra seconds on top of the per-page timeout (model loading)
MORE_TEXT_FACTOR = 1.3  # mixed pages: OCR wins an equal-quality tie if it finds this much more text
QUALITY_TIE = 0.03
WHOLE_FILE = 0  # the key used for files that have no pages


@dataclass
class RouteResult:
    pages: list[Page] = field(default_factory=list)
    decisions: list[Decision] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class _Candidate:
    """One engine's output for one page (or for the whole file), with its log line."""

    pages: list[Page]  # one page for PDFs; all pages (sheets, slides) for a whole file
    quality: float
    decision: Decision

    @property
    def engine(self) -> str | None:
        return self.pages[0].engine

    @property
    def accepted(self) -> bool:
        return self.decision.outcome == "accepted"

    @property
    def chars(self) -> int:
        return sum(len("".join(p.text.split())) for p in self.pages)


class Router:
    def __init__(self, config: Config | None = None, engines: dict[str, Engine] | None = None):
        self.config = config or Config()
        self.engines = engines if engines is not None else build_engines(self.config)
        self._decisions: list[Decision] = []
        self._timed_out: set[str] = set()  # engines that hung on this file: not used again
        self._loaded_heavy: Engine | None = None

    # ------------------------------------------------------------------ public

    def route(self, data: bytes, info: FileInfo) -> RouteResult:
        """Parse one file. Engine problems never raise: they become decisions in the log."""
        self._decisions = []
        self._timed_out = set()
        result = RouteResult()
        if info.file_type == "pdf":
            result.pages = self._route_pdf(data, result)
        elif info.file_type in IMAGE_TYPES:
            result.pages = self._route_whole_file(data, info.file_type, PageKind.SCANNED, result)
        else:
            result.pages = self._route_whole_file(data, info.file_type, None, result)
        result.decisions = self._decisions
        return result

    def runnable_engines(self, info: FileInfo) -> list[str]:
        """Names of installed engines that can read this kind of file (used by the compare tool)."""
        return [
            name
            for name, engine in self.engines.items()
            if engine.missing_reason() is None and _can_read(engine, info)
        ]

    def run_engine(self, name: str, data: bytes, info: FileInfo) -> EngineRun:
        """Run ONE engine alone on the whole file: no triage, no fallback, no merging.

        This is for comparing engines. It still has the time limit, and it never raises.
        """
        self._decisions, self._timed_out = [], set()
        engine = self.engines.get(name)
        if engine is None:
            return EngineRun(engine=name, status="skipped", reason="unknown engine")
        if (reason := engine.missing_reason()) is not None:
            return EngineRun(engine=name, status="skipped", reason=f"not available: {reason}")
        if not _can_read(engine, info):
            return EngineRun(
                engine=name, status="skipped", reason=f"does not handle {info.file_type} files"
            )
        count = max(1, info.page_count or 1) if info.file_type == "pdf" else 1
        todo = list(range(1, count + 1)) if info.file_type == "pdf" else [WHOLE_FILE]
        outcome = self._call(engine, data, info.file_type, None, todo)
        if outcome is None:  # _call already wrote the reason into the log
            last = self._decisions[-1]
            return EngineRun(
                engine=name, status=last.outcome, reason=last.reason, seconds=last.seconds
            )
        pages, seconds_each = outcome
        seconds = round(seconds_each * len(todo), 3)
        text = "\n\n".join(p.text for p in pages if p.text)
        for page in pages:
            page.engine = name
            page.quality = round(quality_report(page.text).score, 3) if page.text else None
        if not text.strip():
            return EngineRun(
                engine=name, status="empty", reason="no text in the output", seconds=seconds
            )
        return EngineRun(
            engine=name,
            status="ok",
            pages=pages,
            quality=round(quality_report(text).score, 3),
            chars=len("".join(text.split())),
            seconds=seconds,
        )

    def release_all(self) -> None:
        """Free every engine's model memory."""
        for engine in self.engines.values():
            engine.release()
        self._loaded_heavy = None

    def engine_report(self) -> list[dict[str, str]]:
        """Name, license and whether it is usable (for `doctordoc engines`)."""
        rows = []
        for engine in self.engines.values():
            reason = engine.missing_reason()
            rows.append(
                {
                    "name": engine.name,
                    "license": engine.license,
                    "status": "ready" if reason is None else f"missing: {reason}",
                    "install": engine.install_hint,
                    "note": engine.note,
                }
            )
        return rows

    # ------------------------------------------------------------------ per file type

    def _route_whole_file(
        self, data: bytes, file_type: str, kind: PageKind | None, result: RouteResult
    ) -> list[Page]:
        chain = self.config.file_chains.get(file_type, [])
        if not chain:
            result.warnings.append(f"No engine chain is configured for '{file_type}'.")
            return []
        candidates = self._run_chain(data, file_type, None, kind, chain)
        pages = self._finish(candidates, [WHOLE_FILE], result)
        for page in pages:
            page.kind = kind or PageKind.NOT_PAGED  # images count as scanned pages
        return pages

    def _route_pdf(self, data: bytes, result: RouteResult) -> list[Page]:
        triage = triage_pdf(data, self.config)
        counts = Counter(t.kind.value for t in triage)
        summary = ", ".join(f"{n} {kind}" for kind, n in counts.items())
        self._log("file", "triage", "accepted", f"{len(triage)} pages: {summary}")

        chosen: dict[int, Page] = {}
        for item in triage:
            if item.kind == PageKind.BLANK:
                chosen[item.number] = Page(number=item.number, kind=PageKind.BLANK)
                self._log(f"page {item.number}", "triage", "skipped", "blank page")

        for kind in (PageKind.DIGITAL, PageKind.MIXED, PageKind.GARBLED, PageKind.SCANNED):
            numbers = [t.number for t in triage if t.kind == kind]
            if not numbers:
                continue
            if kind == PageKind.MIXED:
                candidates = self._run_mixed(data, numbers)
            else:
                chain = self.config.page_chains.get(kind.value, [])
                candidates = self._run_chain(data, "pdf", numbers, kind, chain)
            for page in self._finish(candidates, numbers, result):
                page.kind = kind
                chosen[page.number] = page

        for item in triage:
            if item.rotation and item.number in chosen:
                chosen[item.number].warnings.append(
                    f"Page is rotated {item.rotation} degrees in the PDF (handled when rendering)."
                )
        return [chosen[n] for n in sorted(chosen)]

    # ------------------------------------------------------------------ running chains

    def _run_chain(
        self,
        data: bytes,
        file_type: str,
        numbers: list[int] | None,
        kind: PageKind | None,
        chain: list[str],
    ) -> dict[int, _Candidate]:
        """Try engines in order until every page has an accepted output.

        Returns the best candidate per page: the accepted one, or else the best rejected one.
        `numbers` is None for files without pages (they use the key WHOLE_FILE).
        """
        wanted = list(numbers) if numbers is not None else [WHOLE_FILE]
        best: dict[int, _Candidate] = {}
        for name in chain:
            todo = [n for n in wanted if not (n in best and best[n].accepted)]
            if not todo:
                break
            engine = self._usable_engine(name, file_type, kind, todo)
            if engine is None:
                continue
            pages_to_read = todo if numbers is not None else None
            outcome = self._call(engine, data, file_type, pages_to_read, todo)
            if outcome is None:
                continue
            outputs, seconds_each = outcome
            for number in todo:
                candidate = self._judge(engine, number, outputs, numbers is None, seconds_each)
                if candidate is not None and _is_better(candidate, best.get(number)):
                    best[number] = candidate
        return best

    def _run_mixed(self, data: bytes, numbers: list[int]) -> dict[int, _Candidate]:
        """Mixed pages: run the text chain AND the OCR chain, keep the better result per page."""
        text = self._run_chain(
            data, "pdf", numbers, PageKind.MIXED, self.config.page_chains.get("mixed", [])
        )
        # the scanned chain may end with pdf_text (a last resort for garbled pages): skip it here
        ocr_only = [e for e in self.config.page_chains.get("scanned", []) if e != "pdf_text"]
        ocr = self._run_chain(data, "pdf", numbers, PageKind.MIXED, ocr_only)
        chosen: dict[int, _Candidate] = {}
        for number in numbers:
            from_text, from_ocr = text.get(number), ocr.get(number)
            if from_text is None or from_ocr is None:
                found = from_text or from_ocr
                if found is not None:
                    chosen[number] = found
                continue
            winner, loser = (
                (from_ocr, from_text)
                if _ocr_beats_text(from_ocr, from_text)
                else (from_text, from_ocr)
            )
            summary = (  # written BEFORE merging, because merging changes the winner
                f"mixed page: {winner.engine} was better "
                f"(quality {winner.quality:.2f}, {winner.chars} chars vs {loser.chars})"
            )
            extra = self._merge_extra_content(winner, loser) if self.config.merge_mixed_pages else 0
            loser.decision.outcome = "merged" if extra else "rejected"
            loser.decision.reason = summary + (
                f"; kept {extra} extra block(s) that only {loser.engine} found" if extra else ""
            )
            winner.decision.reason = f"mixed page: kept over {loser.engine}"
            chosen[number] = winner
        return chosen

    def _merge_extra_content(self, winner: _Candidate, loser: _Candidate) -> int:
        """Copy into a text-layer winner the blocks that only OCR found (e.g. text inside a
        picture). Returns how many.

        Never the other way round: OCR reads everything that is visible, so text that only the
        text layer has is hidden text (often junk from a bad OCR layer), and must not be added.
        """
        if not self._reads_text_layer(winner) or self._reads_text_layer(loser):
            return 0
        page, other = winner.pages[0], loser.pages[0]
        added = merge_missing_blocks(page, other)
        if added:
            page.engine = f"{winner.engine}+{loser.engine}"
            page.quality = round(quality_report(page.text).score, 3)
        return added

    def _reads_text_layer(self, candidate: _Candidate) -> bool:
        engine = self.engines.get(candidate.engine or "")
        return engine is not None and engine.uses_text_layer

    def _usable_engine(
        self, name: str, file_type: str, kind: PageKind | None, todo: list[int]
    ) -> Engine | None:
        """Return the engine if it can run now. Otherwise log why not and return None."""
        scope = _scope(todo)
        engine = self.engines.get(name)
        if engine is None:
            self._log(scope, name, "skipped", "unknown engine name in the chain")
        elif name in self._timed_out:
            self._log(scope, name, "skipped", "timed out earlier on this file")
        elif (reason := engine.missing_reason()) is not None:
            hint = f" ({engine.install_hint})" if engine.install_hint else ""
            self._log(scope, name, "skipped", f"not available: {reason}{hint}")
        elif not engine.supports(file_type, kind):
            self._log(scope, name, "skipped", f"does not handle {file_type} / {kind}")
        else:
            return engine
        return None

    # ------------------------------------------------------------------ one engine call

    def _call(
        self,
        engine: Engine,
        data: bytes,
        file_type: str,
        pages_to_read: list[int] | None,
        todo: list[int],
    ) -> tuple[list[Page], float] | None:
        """Run one engine with a time limit. Returns (pages, seconds per page) or None."""
        self._free_other_heavy_engines(engine)
        page_count = max(1, len(todo))
        limit = MODEL_LOAD_ALLOWANCE + self.config.timeouts.get(engine.name, 60.0) * page_count
        box: dict[str, object] = {}

        def work() -> None:
            try:
                box["pages"] = engine.parse(data, file_type, pages_to_read)
            except Exception as err:  # engines may raise anything: the router must survive
                box["error"] = err

        started = time.perf_counter()
        # A daemon thread, so a hung engine cannot keep the program from exiting.
        worker = threading.Thread(target=work, daemon=True, name=f"engine-{engine.name}")
        worker.start()
        worker.join(limit)
        elapsed = time.perf_counter() - started

        scope = _scope(todo)
        if worker.is_alive():
            self._timed_out.add(engine.name)
            self._log(
                scope, engine.name, "timeout", f"no answer after {limit:.0f}s", seconds=elapsed
            )
            return None
        if "error" in box:
            error = box["error"]
            log.debug("engine %s failed", engine.name, exc_info=True)
            self._log(
                scope,
                engine.name,
                "failed",
                f"{type(error).__name__}: {error}"[:300],
                seconds=elapsed,
            )
            return None
        return box["pages"], elapsed / page_count  # type: ignore[return-value]

    def _free_other_heavy_engines(self, engine: Engine) -> None:
        """Only one heavy model lives in memory at a time (important on a 4 GB GPU)."""
        if not engine.heavy:
            return
        if self._loaded_heavy is not None and self._loaded_heavy is not engine:
            self._loaded_heavy.release()
        self._loaded_heavy = engine

    # ------------------------------------------------------------------ judging

    def _judge(
        self, engine: Engine, number: int, outputs: list[Page], whole_file: bool, seconds: float
    ) -> _Candidate | None:
        """Score an engine's output for one page. Returns None if it produced no text."""
        scope = "file" if whole_file else f"page {number}"
        produced = outputs if whole_file else [p for p in outputs if p.number == number]
        text = "\n\n".join(p.text for p in produced if p.text)
        if not text.strip():
            self._log(scope, engine.name, "rejected", "no text in the output", 0.0, seconds)
            return None
        report = quality_report(text)
        ok = report.score >= self.config.quality_threshold
        reason = "passed the quality check" if ok else f"low quality: {report.reason}"
        if turns := sorted({p.turned for p in produced if p.turned}):
            reason += f"; turned {', '.join(map(str, turns))} degrees clockwise before OCR"
        decision = self._log(
            scope,
            engine.name,
            "accepted" if ok else "rejected",
            reason,
            report.score,
            seconds,
        )
        for page in produced:
            page.engine = engine.name
            page.quality = round(quality_report(page.text).score, 3) if page.text else None
        return _Candidate(pages=produced, quality=report.score, decision=decision)

    def _finish(
        self, candidates: dict[int, _Candidate], numbers: list[int], result: RouteResult
    ) -> list[Page]:
        """Turn candidates into final pages. Without an accepted output, keep the best and warn."""
        pages: list[Page] = []
        for number in numbers:
            scope = "file" if number == WHOLE_FILE else f"page {number}"
            candidate = candidates.get(number)
            if candidate is None:
                note = "No text could be extracted: no engine produced usable output."
                pages.append(Page(number=max(number, 1), warnings=[note]))
                result.warnings.append(f"{scope}: {note}")
                continue
            if not candidate.accepted:
                note = (
                    f"Low-quality output kept (quality {candidate.quality:.2f} from "
                    f"{candidate.engine}): a human should check this."
                )
                candidate.decision.outcome = "fallback"
                candidate.decision.reason = (
                    "best of the rejected outputs; " + candidate.decision.reason
                )
                candidate.pages[0].warnings.append(note)
                result.warnings.append(f"{scope}: {note}")
            pages.extend(candidate.pages)
        return pages

    # ------------------------------------------------------------------ logging

    def _log(
        self,
        scope: str,
        engine: str,
        outcome: str,
        reason: str = "",
        quality: float | None = None,
        seconds: float = 0.0,
    ) -> Decision:
        decision = Decision(
            scope=scope,
            engine=engine,
            outcome=outcome,
            reason=reason,
            quality=quality,
            seconds=round(seconds, 3),
        )
        self._decisions.append(decision)
        log.info("%s | %s | %s | %s", scope, engine, outcome, reason)
        return decision


# ---------------------------------------------------------------------------- helpers


def _can_read(engine: Engine, info: FileInfo) -> bool:
    """Could this engine handle the file, for ANY kind of page? (Docling only takes digital
    pages in the router, but for comparing we let it try whole PDFs.)"""
    if info.file_type == "pdf":
        return any(
            engine.supports("pdf", kind)
            for kind in (PageKind.DIGITAL, PageKind.MIXED, PageKind.SCANNED)
        )
    kind = PageKind.SCANNED if info.file_type in IMAGE_TYPES else None
    return engine.supports(info.file_type, kind)


def _is_better(new: _Candidate, old: _Candidate | None) -> bool:
    """An accepted candidate beats a rejected one. Between equals, higher quality wins."""
    if old is None:
        return True
    if old.accepted:
        return False  # we never replace an accepted result
    if new.accepted:
        return True
    return (new.quality, new.chars) > (old.quality, old.chars)


def _ocr_beats_text(ocr: _Candidate, text: _Candidate) -> bool:
    """On a mixed page, OCR must be clearly better, or equal quality with much more text."""
    if ocr.quality > text.quality + QUALITY_TIE:
        return True
    tie = abs(ocr.quality - text.quality) <= QUALITY_TIE
    return tie and ocr.chars > MORE_TEXT_FACTOR * max(text.chars, 1)


def _scope(numbers: list[int]) -> str:
    """'file', 'page 3', 'pages 1-4' or 'pages 1,3,7' for the decision log."""
    if not numbers or numbers == [WHOLE_FILE]:
        return "file"
    if len(numbers) == 1:
        return f"page {numbers[0]}"
    if numbers == list(range(numbers[0], numbers[-1] + 1)):
        return f"pages {numbers[0]}-{numbers[-1]}"
    return "pages " + ",".join(str(n) for n in numbers)
