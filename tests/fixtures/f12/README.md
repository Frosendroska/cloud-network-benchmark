# F12 numeric oracle

`expected.json` is deliberately independent of the report implementation. It records unequal receiver intervals, raw RTT statistics, and deployment quartiles so analysis tests can check arithmetic without deriving expected values from F12 helpers.

The integration replay tests wrap the existing materialized F08 lifecycle fixture. That fixture writes real F01R artifacts, records source hashes, and calls F08 validation before returning the saved input/result pair; F12 does not manufacture a valid bundle.
