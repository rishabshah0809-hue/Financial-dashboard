# NSE constituent lists — the sector membership backbone

Each `<sector_key>.csv` here is the **official NSE index constituent list** for a
FundaCheck sector, as published by NSE / niftyindices.com. Sector membership
comes from these files only — never from IndianAPI's free-text `industry` field.

- Format: NSE's own `ind_nifty*list.csv` (a `Symbol` column is required).
- Refreshed automatically by `scripts/fetch_nse_constituents.py` inside the
  monthly GitHub Action, from the `source_csv_url` defined for each sector in
  `core/sector_universe.py`.
- Fail-soft: if a download fails, the previously committed CSV is kept; a sector
  with no CSV is reported as *skipped* in the snapshot (its metrics are never
  invented).

Sector → NSE universe mapping (and how exact each one is) is defined in
`core/sector_universe.py` and shown in the Sector Lens:

| Sector | NSE universe | Mapping |
|---|---|---|
| Banking & Finance | NIFTY FINANCIAL SERVICES | approximate |
| IT Services & Software | NIFTY IT | exact |
| FMCG & Consumer Staples | NIFTY FMCG | exact |
| Pharma & Healthcare | NIFTY PHARMA | exact |
| Real Estate | NIFTY REALTY | exact |
| Infrastructure | NIFTY INFRASTRUCTURE | approximate |
| Manufacturing | NIFTY INDIA MANUFACTURING | approximate |
| Retail & Consumer | NIFTY INDIA CONSUMPTION | proxy |
| Diversified / Other | NIFTY 100 | proxy |
