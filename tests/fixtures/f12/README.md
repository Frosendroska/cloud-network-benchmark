# F12 numeric oracle

`expected.json` is deliberately independent of the report implementation. It records unequal receiver intervals, raw RTT statistics, and deployment quartiles so analysis tests can check arithmetic without deriving expected values from F12 helpers.

The integration replay tests wrap the existing materialized F08 lifecycle fixture. That fixture writes real F01R artifacts, records source hashes, and calls F08 validation before returning the saved input/result pair; F12 does not manufacture a valid bundle.

The F12 wrapper adds only exact supplemental artifact declarations and matching child-manifest rows for the already materialized Sockperf observation and build-identity files attached by the F08 helper. Its S1/S3 compatibility test uses a disposable copy of the real S3 config and adapts the F08 helper's synthetic P0 schedule row plus a summary-label assertion; the canonical S3 identity, benchmark settings, and placement evidence are preserved.
