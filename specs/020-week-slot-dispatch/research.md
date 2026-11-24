# Research: Paired WEEK dispatch

- The 20 November Thesis pre-DAY plan requires one bounded, explicitly admitted V-S1/V-S3 pairing through the production WEEK dispatch path, one service and shared ledger, plus all 56 entries, state isolation, partial failure, missed/duplicate slots and restart reconciliation.
- The existing driver already uses a thread pool for same-time WEEK entries; acceptance groups entries serially. Existing 56-entry tests use a synthetic stage and direct real ledger reservation in the fake launch callback, so they do not freeze a weekly-concurrency approval or prove actual provider behavior.
- The updated policy will allow only one S1/P0 and S3/P1 pair per slot, capped at two. Missing or modified policy evidence fails closed.
- A fully cleaned WEEK measurement failure still blocks subsequent slots under this release decision. Both members already admitted in that slot are allowed to settle and preserve cleanup evidence.
- Source baseline: `eeac59498736bff46f1a27bbc63cf4fd4475145f`. The checklist's final Azure source `b86eb70d49196f3a7eb33fee24071d34af5cf2c0` is absent from local Git objects at preparation time.
