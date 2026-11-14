# Local financial card contract
No real approval is supplied in this feature. `FinancialAuthorization.model_json_schema()` is the
machine contract; `financial_binding(plan, request)` gives the exact entry to review after preparation.
The hashed source of `paid_authorization`, not arbitrary wrapper fields, is parsed. Unknown fields,
missing cash caps, offsets, ambiguous dates, changed source bytes, duplicate entries or counts reject.
Local card ownership is a manual trust boundary, not cryptographic human authentication.
No card can enable cumulative SCP enforcement; paid launch always returns the explicit transport blocker.
