"""
quarterly_ocr.py
----------------
The ONLY module in this project that imports an OCR engine.

`core/quarterly_pdf.py` asks this module for text; it never imports paddleocr,
surya or any other engine itself. That boundary exists so the OCR
implementation can be swapped -- local engine today, an external HTTP service
tomorrow -- without the financial parser changing at all.

Contract
--------
Providers return OCR *tokens*: a piece of recognised text, where it sat on the
page, and how confident the engine was. They do not parse, interpret, total or
correct financial figures. Every judgement about what a number MEANS stays in
`quarterly_pdf.py` / `quarterly_semantics.py`, and no language model is
involved at any point.

Providers
---------
    QuarterlyOCRProvider          abstract interface
      +- LocalPaddleOCRProvider   PaddleOCR / PP-StructureV3 in-process
      +- RemoteOCRProvider        same interface over HTTP, for when the
                                  engine cannot live in the app's container
      +- NullOCRProvider          no engine; returns nothing, never raises

`get_provider()` picks one and caches it. Selection is explicit and
inspectable via `provider_status()`, because "OCR silently did nothing" and
"OCR ran and found nothing" must never look the same to the caller.

Configuration (environment)
---------------------------
    FUNDACHECK_OCR_PROVIDER   'auto' (default) | 'local' | 'remote' | 'none'
    FUNDACHECK_OCR_URL        endpoint for the remote provider
    FUNDACHECK_OCR_TOKEN      bearer token for the remote provider
    FUNDACHECK_OCR_TIMEOUT    per-request timeout in seconds (default 60)

All engine imports are lazy and every initialisation path is wrapped: if OCR
is unavailable or fails to start, the application still runs and the affected
cells are reported unavailable rather than guessed.
"""

from __future__ import annotations

import base64
import io
import logging
import os
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

log = logging.getLogger(__name__)

__all__ = [
    "OCRToken", "OCRTable", "OCRPageResult",
    "QuarterlyOCRProvider", "LocalPaddleOCRProvider", "RemoteOCRProvider",
    "NullOCRProvider",
    "get_provider", "reset_provider", "provider_status", "ocr_available",
    "ocr_page", "ocr_pages", "ocr_financial_table",
]

#: below this, a token is treated as needing a second opinion before any
#: financial figure derived from it is presented without a warning.
LOW_CONFIDENCE = 0.85


# ==========================================================================
# data carried back to the parser
# ==========================================================================
@dataclass(frozen=True)
class OCRToken:
    """One recognised run of text and where it sat, in IMAGE pixel space."""

    text: str
    #: (x0, y0, x1, y1) in pixels of the image that was OCR'd
    bbox: tuple[float, float, float, float]
    confidence: float
    engine: str = ""

    @property
    def y_center(self) -> float:
        return (self.bbox[1] + self.bbox[3]) / 2.0

    @property
    def x_center(self) -> float:
        return (self.bbox[0] + self.bbox[2]) / 2.0

    def is_low_confidence(self) -> bool:
        return self.confidence < LOW_CONFIDENCE


@dataclass(frozen=True)
class OCRTable:
    """A structure-recognised table: rows of cell text, plus its own bbox."""

    rows: tuple[tuple[str, ...], ...]
    bbox: tuple[float, float, float, float] | None = None
    confidence: float = 0.0
    engine: str = ""


@dataclass(frozen=True)
class OCRPageResult:
    """Everything one OCR call produced for one image."""

    tokens: tuple[OCRToken, ...] = ()
    tables: tuple[OCRTable, ...] = ()
    engine: str = ""
    #: set when the engine was asked to run but could not
    error: str = ""

    def __bool__(self) -> bool:
        return bool(self.tokens or self.tables)

    @property
    def mean_confidence(self) -> float:
        if not self.tokens:
            return 0.0
        return sum(t.confidence for t in self.tokens) / len(self.tokens)


# ==========================================================================
# provider interface
# ==========================================================================
class QuarterlyOCRProvider(ABC):
    """OCR as the financial parser needs it. Implementations must not raise."""

    name: str = "abstract"

    @abstractmethod
    def available(self) -> bool:
        """True when this provider can actually perform OCR right now."""

    @abstractmethod
    def ocr_page(self, image: Any) -> OCRPageResult:
        """Recognise text on ONE page image (PIL Image or PNG bytes)."""

    def ocr_pages(self, images: Iterable[Any]) -> list[OCRPageResult]:
        """Recognise several page images. Default: one call each."""
        return [self.ocr_page(img) for img in images]

    def ocr_financial_table(self, image: Any) -> OCRPageResult:
        """Recognise a page image as a TABLE, keeping cell structure.

        Default implementation falls back to plain text recognition, which the
        caller can still use positionally. Providers with a structure model
        override this.
        """
        return self.ocr_page(image)

    # -- helpers shared by implementations -------------------------------
    @staticmethod
    def _to_png_bytes(image: Any) -> bytes | None:
        """Normalise a PIL Image / bytes / path into PNG bytes."""
        if image is None:
            return None
        if isinstance(image, (bytes, bytearray)):
            return bytes(image)
        if isinstance(image, str):
            try:
                with open(image, "rb") as fh:
                    return fh.read()
            except OSError:
                return None
        save = getattr(image, "save", None)
        if callable(save):                           # PIL Image
            buf = io.BytesIO()
            try:
                save(buf, format="PNG")
            except Exception:                        # noqa: BLE001
                return None
            return buf.getvalue()
        return None

    @staticmethod
    def _to_pil(image: Any):
        """Normalise into a PIL Image, or None."""
        if image is None:
            return None
        if hasattr(image, "convert"):                # already PIL
            return image.convert("RGB")
        try:
            from PIL import Image
        except Exception:                            # noqa: BLE001
            return None
        if isinstance(image, (bytes, bytearray)):
            try:
                return Image.open(io.BytesIO(bytes(image))).convert("RGB")
            except Exception:                        # noqa: BLE001
                return None
        if isinstance(image, str):
            try:
                return Image.open(image).convert("RGB")
            except Exception:                        # noqa: BLE001
                return None
        return None


# ==========================================================================
# null provider
# ==========================================================================
class NullOCRProvider(QuarterlyOCRProvider):
    """No OCR engine. Returns nothing, explains why, never raises."""

    name = "none"

    def __init__(self, reason: str = "no OCR engine is configured"):
        self.reason = reason

    def available(self) -> bool:
        return False

    def ocr_page(self, image: Any) -> OCRPageResult:
        return OCRPageResult(engine=self.name, error=self.reason)


# ==========================================================================
# local PaddleOCR provider
# ==========================================================================
class LocalPaddleOCRProvider(QuarterlyOCRProvider):
    """PaddleOCR in-process, with PP-StructureV3 for tables when present.

    Supports both the 3.x API (`predict`, `PPStructureV3`) and the 2.x API
    (`ocr(..., cls=True)`), because which one is installed depends on the
    resolver in the deployment environment and the parser must not care.
    """

    name = "local-paddleocr"

    def __init__(self, lang: str = "en"):
        self._lang = lang
        self._engine = None
        self._structure = None
        self._structure_tried = False
        self._tried = False
        self._api = ""                               # '3x' | '2x'
        self._error = ""
        self._lock = threading.Lock()

    # -- initialisation --------------------------------------------------
    def _init(self) -> None:
        if self._tried:
            return
        with self._lock:
            if self._tried:
                return
            self._tried = True
            try:
                import paddleocr                     # noqa: F401
            except Exception as exc:                 # noqa: BLE001
                self._error = f"paddleocr not importable: {exc}"
                log.info("OCR unavailable: %s", self._error)
                return
            self._init_text_engine()
            # PP-StructureV3 is NOT initialised here. It pulls a ~1.2 GB model
            # zoo and takes minutes to load, and the column-wise extraction
            # this parser does needs only text recognition. It is built on
            # first actual use, in ocr_financial_table().

    def _init_text_engine(self) -> None:
        from paddleocr import PaddleOCR
        # PaddleOCR 3.x dropped use_gpu/show_log/use_angle_cls. Try the modern
        # signature first, then degrade, rather than pinning one version.
        for kwargs in (
            {"lang": self._lang, "use_doc_orientation_classify": False,
             "use_doc_unwarping": False, "use_textline_orientation": False},
            {"lang": self._lang},
            {"lang": self._lang, "use_angle_cls": True, "show_log": False},
        ):
            try:
                self._engine = PaddleOCR(**kwargs)
                self._api = "3x" if hasattr(self._engine, "predict") else "2x"
                return
            except Exception as exc:                 # noqa: BLE001
                self._error = f"PaddleOCR init failed: {exc}"
        log.info("OCR text engine unavailable: %s", self._error)

    def _init_structure_engine(self):
        """Build PP-StructureV3 on demand. Optional: text OCR reads a column.

        Deliberately lazy -- loading it costs a ~1.2 GB model download on first
        use and minutes of start-up, which must not be paid by a filing that
        only needs text recognition (or by app start-up).
        """
        if self._structure_tried:
            return self._structure
        with self._lock:
            if self._structure_tried:
                return self._structure
            self._structure_tried = True
            try:
                from paddleocr import PPStructureV3
                self._structure = PPStructureV3()
            except Exception as exc:                 # noqa: BLE001
                log.info("PP-StructureV3 unavailable (text OCR still usable): %s",
                         exc)
                self._structure = None
        return self._structure

    # -- interface -------------------------------------------------------
    def available(self) -> bool:
        self._init()
        return self._engine is not None

    def status(self) -> dict:
        self._init()
        return {
            "provider": self.name,
            "text_engine": bool(self._engine),
            # not initialised until ocr_financial_table() is called
            "structure_engine": bool(self._structure),
            "structure_loaded": self._structure_tried,
            "api": self._api,
            "error": self._error,
        }

    def ocr_page(self, image: Any) -> OCRPageResult:
        self._init()
        if self._engine is None:
            return OCRPageResult(engine=self.name,
                                 error=self._error or "engine not initialised")
        pil = self._to_pil(image)
        if pil is None:
            return OCRPageResult(engine=self.name, error="unreadable image")
        try:
            import numpy as np
            arr = np.array(pil)
        except Exception as exc:                     # noqa: BLE001
            return OCRPageResult(engine=self.name, error=f"numpy unavailable: {exc}")

        try:
            raw = (self._engine.predict(arr) if self._api == "3x"
                   else self._engine.ocr(arr, cls=True))
        except Exception as exc:                     # noqa: BLE001
            log.warning("OCR page failed: %s", exc)
            return OCRPageResult(engine=self.name, error=str(exc))

        tokens = (self._parse_3x(raw) if self._api == "3x" else self._parse_2x(raw))
        return OCRPageResult(tokens=tuple(tokens), engine=self.name)

    def ocr_financial_table(self, image: Any) -> OCRPageResult:
        self._init()
        if self._engine is None:
            return OCRPageResult(engine=self.name,
                                 error=self._error or "engine not initialised")
        if self._init_structure_engine() is None:
            return self.ocr_page(image)
        pil = self._to_pil(image)
        if pil is None:
            return OCRPageResult(engine=self.name, error="unreadable image")
        try:
            import numpy as np
            raw = self._structure.predict(np.array(pil))
        except Exception as exc:                     # noqa: BLE001
            log.warning("PP-Structure failed, falling back to text OCR: %s", exc)
            return self.ocr_page(image)
        tables = tuple(self._parse_tables(raw))
        text = self.ocr_page(image)
        return OCRPageResult(tokens=text.tokens, tables=tables, engine=self.name)

    # -- result parsing --------------------------------------------------
    def _parse_3x(self, raw: Any) -> list[OCRToken]:
        """PaddleOCR 3.x returns a list of dict-like results per image."""
        out: list[OCRToken] = []
        for res in (raw or []):
            data = getattr(res, "json", None) or res
            if isinstance(data, dict) and "res" in data:
                data = data["res"]
            if not isinstance(data, dict):
                continue
            polys = (data.get("rec_polys") or data.get("dt_polys")
                     or data.get("rec_boxes") or [])
            texts = data.get("rec_texts") or []
            scores = data.get("rec_scores") or []
            for i, text in enumerate(texts):
                if not str(text).strip():
                    continue
                bbox = self._poly_to_bbox(polys[i]) if i < len(polys) else (0, 0, 0, 0)
                conf = float(scores[i]) if i < len(scores) else 0.0
                out.append(OCRToken(str(text), bbox, conf, self.name))
        return out

    def _parse_2x(self, raw: Any) -> list[OCRToken]:
        """PaddleOCR 2.x returns [[ [box, (text, score)], ... ]]."""
        out: list[OCRToken] = []
        pages = raw or []
        for page in pages:
            for line in (page or []):
                try:
                    box, (text, score) = line
                except Exception:                    # noqa: BLE001
                    continue
                if not str(text).strip():
                    continue
                out.append(OCRToken(str(text), self._poly_to_bbox(box),
                                    float(score), self.name))
        return out

    def _parse_tables(self, raw: Any) -> list[OCRTable]:
        out: list[OCRTable] = []
        for res in (raw or []):
            data = getattr(res, "json", None) or res
            if isinstance(data, dict) and "res" in data:
                data = data["res"]
            if not isinstance(data, dict):
                continue
            for tbl in (data.get("table_res_list") or []):
                html = (tbl or {}).get("pred_html") or ""
                rows = self._rows_from_html(html)
                if rows:
                    out.append(OCRTable(rows=rows, engine=self.name))
        return out

    @staticmethod
    def _rows_from_html(html: str) -> tuple[tuple[str, ...], ...]:
        """Extract cell text from PP-Structure's HTML, without a parser dep."""
        if not html:
            return ()
        import re
        rows: list[tuple[str, ...]] = []
        for rm in re.finditer(r"<tr>(.*?)</tr>", html, re.S | re.I):
            cells = [re.sub(r"<[^>]+>", "", c).strip()
                     for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>",
                                         rm.group(1), re.S | re.I)]
            if cells:
                rows.append(tuple(cells))
        return tuple(rows)

    @staticmethod
    def _poly_to_bbox(poly: Any) -> tuple[float, float, float, float]:
        try:
            pts = list(poly)
            if len(pts) == 4 and all(isinstance(v, (int, float)) for v in pts):
                x0, y0, x1, y1 = (float(v) for v in pts)
                return (x0, y0, x1, y1)
            xs = [float(p[0]) for p in pts]
            ys = [float(p[1]) for p in pts]
            return (min(xs), min(ys), max(xs), max(ys))
        except Exception:                            # noqa: BLE001
            return (0.0, 0.0, 0.0, 0.0)


# ==========================================================================
# remote provider
# ==========================================================================
class RemoteOCRProvider(QuarterlyOCRProvider):
    """The same contract over HTTP, for when the engine cannot live in-process.

    Exists so that a deployment which cannot host PaddleOCR (disk, memory or
    wheel constraints) can point at a service instead WITHOUT the financial
    parser changing. The wire format is deliberately minimal:

        POST <url>
        {"image_png_b64": "...", "mode": "text" | "table"}
        ->
        {"tokens": [{"text","bbox":[x0,y0,x1,y1],"confidence"}],
         "tables": [{"rows": [[...], ...]}]}

    Note: this sends the page image to whatever endpoint is configured. It is
    inert unless FUNDACHECK_OCR_URL is explicitly set.
    """

    name = "remote-ocr"

    def __init__(self, url: str = "", token: str = "", timeout: float = 60.0):
        self._url = url or os.environ.get("FUNDACHECK_OCR_URL", "")
        self._token = token or os.environ.get("FUNDACHECK_OCR_TOKEN", "")
        try:
            self._timeout = float(timeout or
                                  os.environ.get("FUNDACHECK_OCR_TIMEOUT", 60))
        except (TypeError, ValueError):
            self._timeout = 60.0

    def available(self) -> bool:
        return bool(self._url)

    def status(self) -> dict:
        return {"provider": self.name, "configured": bool(self._url),
                "url": self._url}

    def ocr_page(self, image: Any) -> OCRPageResult:
        return self._call(image, "text")

    def ocr_financial_table(self, image: Any) -> OCRPageResult:
        return self._call(image, "table")

    def _call(self, image: Any, mode: str) -> OCRPageResult:
        if not self._url:
            return OCRPageResult(engine=self.name,
                                 error="FUNDACHECK_OCR_URL is not set")
        png = self._to_png_bytes(image)
        if png is None:
            return OCRPageResult(engine=self.name, error="unreadable image")
        try:
            import requests
        except Exception as exc:                     # noqa: BLE001
            return OCRPageResult(engine=self.name, error=f"requests missing: {exc}")

        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        try:
            resp = requests.post(
                self._url, headers=headers, timeout=self._timeout,
                json={"image_png_b64": base64.b64encode(png).decode("ascii"),
                      "mode": mode})
            resp.raise_for_status()
            payload = resp.json()
        except Exception as exc:                     # noqa: BLE001
            log.warning("remote OCR call failed: %s", exc)
            return OCRPageResult(engine=self.name, error=str(exc))
        return self._parse_payload(payload)

    def _parse_payload(self, payload: Any) -> OCRPageResult:
        if not isinstance(payload, dict):
            return OCRPageResult(engine=self.name, error="malformed response")
        tokens: list[OCRToken] = []
        for raw in (payload.get("tokens") or []):
            if not isinstance(raw, dict):
                continue
            text = str(raw.get("text", "")).strip()
            if not text:
                continue
            bb = raw.get("bbox") or [0, 0, 0, 0]
            try:
                bbox = (float(bb[0]), float(bb[1]), float(bb[2]), float(bb[3]))
            except Exception:                        # noqa: BLE001
                bbox = (0.0, 0.0, 0.0, 0.0)
            try:
                conf = float(raw.get("confidence", 0.0))
            except (TypeError, ValueError):
                conf = 0.0
            tokens.append(OCRToken(text, bbox, conf, self.name))
        tables: list[OCRTable] = []
        for raw in (payload.get("tables") or []):
            rows = (raw or {}).get("rows") or []
            parsed = tuple(tuple(str(c) for c in row) for row in rows if row)
            if parsed:
                tables.append(OCRTable(rows=parsed, engine=self.name))
        return OCRPageResult(tuple(tokens), tuple(tables), engine=self.name)


# ==========================================================================
# selection
# ==========================================================================
_PROVIDER: QuarterlyOCRProvider | None = None
_PROVIDER_LOCK = threading.Lock()


def _build_provider() -> QuarterlyOCRProvider:
    choice = (os.environ.get("FUNDACHECK_OCR_PROVIDER") or "auto").strip().lower()

    if choice == "none":
        return NullOCRProvider("OCR disabled by FUNDACHECK_OCR_PROVIDER=none")
    if choice == "remote":
        remote = RemoteOCRProvider()
        return remote if remote.available() else NullOCRProvider(
            "FUNDACHECK_OCR_PROVIDER=remote but FUNDACHECK_OCR_URL is not set")
    if choice == "local":
        local = LocalPaddleOCRProvider()
        return local if local.available() else NullOCRProvider(
            local.status().get("error") or "PaddleOCR is not installed")

    # auto: prefer a configured remote service, else a local engine, else none.
    remote = RemoteOCRProvider()
    if remote.available():
        return remote
    local = LocalPaddleOCRProvider()
    if local.available():
        return local
    return NullOCRProvider(
        local.status().get("error")
        or "no OCR engine installed and FUNDACHECK_OCR_URL is not set")


def get_provider() -> QuarterlyOCRProvider:
    global _PROVIDER
    if _PROVIDER is None:
        with _PROVIDER_LOCK:
            if _PROVIDER is None:
                try:
                    _PROVIDER = _build_provider()
                except Exception as exc:             # noqa: BLE001
                    log.warning("OCR provider selection failed: %s", exc)
                    _PROVIDER = NullOCRProvider(f"provider selection failed: {exc}")
    return _PROVIDER


def reset_provider() -> None:
    """Drop the cached provider (tests, and after changing configuration)."""
    global _PROVIDER
    with _PROVIDER_LOCK:
        _PROVIDER = None


def provider_status() -> dict:
    """What OCR is in use, for the data-quality panel and for diagnostics."""
    provider = get_provider()
    status = getattr(provider, "status", None)
    base = status() if callable(status) else {"provider": provider.name}
    base["available"] = provider.available()
    if isinstance(provider, NullOCRProvider):
        base["reason"] = provider.reason
    return base


def ocr_available() -> bool:
    return get_provider().available()


# -- module-level convenience wrappers -------------------------------------
def ocr_page(image: Any) -> OCRPageResult:
    return get_provider().ocr_page(image)


def ocr_pages(images: Iterable[Any]) -> list[OCRPageResult]:
    return get_provider().ocr_pages(images)


def ocr_financial_table(image: Any) -> OCRPageResult:
    return get_provider().ocr_financial_table(image)


# ==========================================================================
# geometry helper used by the PDF parser
# ==========================================================================
def tokens_to_pdf_space(tokens: Sequence[OCRToken], *, origin_y: float,
                        zoom: float) -> list[tuple[float, str, float]]:
    """Map image-space tokens back onto the PDF page's coordinate system.

    Returns [(y_center_in_pdf_points, text, confidence)] sorted top-to-bottom,
    which is the shape `quarterly_pdf._nearest_ocr` consumes.
    """
    out = [(origin_y + t.y_center / zoom, t.text, t.confidence)
           for t in tokens if str(t.text).strip()]
    out.sort(key=lambda r: r[0])
    return out
