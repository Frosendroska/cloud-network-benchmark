# Acquisition parser fixtures

These fixtures are synthetic parser test inputs, not measurements and not output
from either pinned executable. The matching upstream code and modified binary must
be identified separately before any real record can be accepted.

Each line uses the source-owned `CNB_ACQ_V1 ` prefix and UTF-8 JSON followed by LF.
Offsets in tests refer to these exact bytes. A missing final LF is intentionally an
invalid/truncated record. Native stderr lines without the prefix remain preserved
and ignored by the acquisition parser.
