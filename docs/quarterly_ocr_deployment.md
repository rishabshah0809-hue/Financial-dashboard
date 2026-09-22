# Quarterly OCR — architecture and deployment

## Why this exists

Some Indian quarterly-results filings publish their financial tables as
scanned images. Native text extraction returns nothing usable for those, and
the engine's rule is that an unreadable cell stays unavailable (`—`) rather
than being guessed. OCR is the only way to turn those filings into data.

No language model is involved at any point. OCR recognises characters; every
decision about what a number *means* stays in `quarterly_pdf.py` /
`quarterly_semantics.py`.

## Architecture

```
native PDF text + geometry
        │
        ├─ column readable ──────────────────────────► value (source: pdf_raw:text)
        │
        └─ column rasterised
                 │
                 ▼
        core/quarterly_ocr.get_provider()
                 │
        ┌────────┴─────────┬──────────────────┐
        ▼                  ▼                  ▼
 LocalPaddleOCR      RemoteOCRProvider   NullOCRProvider
  (in-process)          (HTTP)            (no engine)
        │                  │                  │
        └────────┬─────────┴──────────────────┘
                 ▼
          OCRPageResult(tokens, tables)
                 │
                 ▼
    tokens_to_pdf_space()  →  existing quarterly parser
                              →  Python calculations
                              →  PDF-reported-ratio validation
```

`core/quarterly_ocr.py` is the **only** module that imports an OCR engine;
`tests/test_quarterly_ocr.py::ParserIntegration` enforces that. The parser
calls `_ocr_column()`, which delegates to whichever provider is configured, so
moving OCR out of the container later changes no parsing code.

### OCR is invoked conditionally

`_extract_results_table` builds `image_cols` from the periods whose column
`source` is `"image"`. Only those regions are rendered and sent to OCR — never
whole pages, never a column the text layer already read. A filing that is
entirely native text makes zero OCR calls.

### OCR never silently overwrites a native value

`_record()` in `_extract_results_table` is first-match-wins per (row, period)
cell, and text columns and image columns are disjoint by construction, so an
OCR token cannot replace a figure taken from the text layer.

### Provenance

Every OCR-derived cell carries `pdf_raw:ocr`, plus `:low_conf(<score>)` when
the engine's confidence was below 0.85. That flows into
`meta["raw_provenance"][row][period]` alongside `value`, `period`, `unit`,
`validation` and `confidence`, and `meta["ocr_status"]` records which provider
was live for the parse. `meta["source_scope"]` carries scope.

## Configuration

| Variable | Values | Meaning |
|---|---|---|
| `FUNDACHECK_OCR_PROVIDER` | `auto` (default), `local`, `remote`, `none` | which provider to use |
| `FUNDACHECK_OCR_URL` | URL | endpoint for `RemoteOCRProvider` |
| `FUNDACHECK_OCR_TOKEN` | string | bearer token for that endpoint |
| `FUNDACHECK_OCR_TIMEOUT` | seconds (default 60) | per-request timeout |

`auto` prefers a configured remote service, falls back to a local engine, and
finally to the null provider. Selection never raises; `provider_status()`
reports what happened and the app's Data quality panel shows it.

On Streamlit Community Cloud these go in **Settings → Secrets**, which are
exposed to the app as environment variables.

## Deploying the local engine

`requirements.txt` does **not** include the OCR stack, deliberately: a failed
build takes down the whole app, including the annual/Excel workflow that needs
no OCR. The pins live in `requirements-ocr.txt`.

To try it on Community Cloud, append these to `requirements.txt` and redeploy:

```
paddlepaddle==3.3.1
paddleocr==3.7.0
pandas>=2.2,<3
numpy>=1.26,<3
```

The `pandas`/`numpy` ceilings matter: `paddlex` otherwise resolves the app onto
pandas 3.x, a breaking major release the annual/Excel code is not tested
against.

### Measured footprint (Python 3.12, which `runtime.txt` pins)

| Item | Size |
|---|---|
| `paddlepaddle` 3.3.1 linux wheel | 195 MB compressed |
| venv after `paddlepaddle` + `paddleocr` | 880 MB |
| venv after adding `paddlex[ocr]` (PP-StructureV3) | 1.1 GB |
| PP-OCR / PP-Structure model files | 295 MB+, downloaded on **first use** |

Roughly **1.4 GB** on top of streamlit/pandas/plotly.

Two operational consequences:

* **Models download at first use, not at build time.** The first scanned filing
  a user opens pays a one-off delay (about two minutes observed locally) and
  the container needs outbound network access plus room for the files. They
  land in `~/.paddlex`, which is not persisted across Community Cloud restarts,
  so the cost recurs after every redeploy or sleep.
* **PP-StructureV3 is optional.** It needs the `paddlex[ocr]` extra (~200 MB
  more) and loads a sizeable model zoo. Without it, PaddleOCR still performs
  plain text recognition, which is enough to read a rasterised column
  positionally — `LocalPaddleOCRProvider` degrades to that automatically.

### If the local engine does not fit

Community Cloud's published limit is per-app RAM in the low gigabytes, and the
footprint above is large relative to it. If the build fails or the app is
killed:

1. Remove the OCR lines from `requirements.txt` again.
2. Stand up an OCR service exposing the wire format below.
3. Set `FUNDACHECK_OCR_PROVIDER=remote` and `FUNDACHECK_OCR_URL` in Secrets.

No parser code changes. The service is not built in this repository yet.

#### Remote wire format

```
POST <FUNDACHECK_OCR_URL>
Authorization: Bearer <FUNDACHECK_OCR_TOKEN>      (optional)
{
  "image_png_b64": "<base64 PNG of one page region>",
  "mode": "text" | "table"
}

200 OK
{
  "tokens": [
    {"text": "72,275", "bbox": [x0, y0, x1, y1], "confidence": 0.97}
  ],
  "tables": [
    {"rows": [["Revenue from operations", "72,275"], ["..."]]}
  ]
}
```

`bbox` is in pixels of the submitted image. The caller maps it back to PDF
points with `tokens_to_pdf_space()`.

Note that this sends page images to the configured endpoint; it is inert unless
`FUNDACHECK_OCR_URL` is set.

## Behaviour with no OCR at all

This is the default and it is a supported state, not a broken one:

* the app starts normally and the annual/Excel workflow is unaffected;
* fully-scanned filings (BEL, Jash) raise `QuarterlyPDFError` with an
  explanation rather than returning partial numbers;
* a filing with one rasterised column (Reliance) returns the readable column
  and marks the other unavailable, reports `validation_detail` status
  `insufficient_data`, and forces overall confidence to `low`;
* a filing whose consolidated section is scanned but whose standalone section
  is text (Anand Rathi) falls back to standalone, labels the scope, and records
  `scope_fallback_reason`.
