# F11 offline fixtures

`cost-inputs.json` is a deliberately scoped selection derived from bounded register extracts, not a workbook export. The source extract was acquired 2026-10-21 and covered `Budget!A1:H35`, `Runs!A1:Z40`, and `Cost model!A1:J65` (SHA-256 `a1683a880dc0ab5556cafd5f2faae7f7cccda41fa9ee22d9dff90dd485f1195c`). Its paired continuation covers `Runs!A41:I100` (SHA-256 `6a091f5f02f550ba4017c2d2e1049fdee536f66c180c6f2f233ce3a4e46e9b59`); that extract has no independent acquisition date. The input prices were checked 2026-10-14. The selected payload has its own SHA-256 and makes no claim about a workbook revision, full formulas, live pricing or actual charges.

The register contained 63 Required, nine Conditional and nine Deferred rows, including 28 M-S1 and 28 M-S3 entries. Forecast components are $100.40 primary, $1.95 replacements, $20.47 tax and $25 uncertainty = $147.82; the $50 protected buffer is outside the $250 working allocation. Optional P2/P3 illustrations are excluded. These numbers are a dated forecast only.

The schedule CSV is an inspectable pending example. It deliberately has no invented study dates or common N and cannot authorize a launch.
