# NSE constituent lists — the sector membership backbone

Each <sector_key>.csv here is the **official NSE index constituent list** for a
FundaCheck sector, as published by NSE / niftyindices.com. Sector membership
comes from these files only — never from IndianAPI's free-text industry field.

- Format: NSE's own ind_nifty*list.csv (a Symbol column is required).
- Refreshed automatically by scripts/fetch_nse_constituents.py inside the
  monthly GitHub Action, from the source_csv_url defined for each sector in
  core/sector_universe.py.
- Fail-soft: if a download fails, the previously committed CSV is kept; a sector
  with no CSV is reported as *skipped* in the snapshot (its metrics are never
  invented).
