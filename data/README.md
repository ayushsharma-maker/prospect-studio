# data/

- `hubspot_customers.csv` — companies to skip (your existing customers). Export from HubSpot
  (Companies › Export) keeping `Company Domain Name` and `Lifecycle Stage`. Only rows whose stage is
  `customer` or `evangelist` are excluded; leads stay researchable. The file shipped here is an
  illustrative stand-in: replace it with your own export.
- `hubspot_customers_standin.csv` — the same stand-in, kept as an example of the layout.
- `sample_domains.txt` — test domains, one per line, optionally `domain, Company Name`.
- `competitor_customers.csv` — optional extra exclusion list (`domain` column).
