# Feature Specification: DAY artifact identity

**Feature Branch**: `codex/f01r-day-identity`

**Created**: 2026-10-23

**Status**: Specified and clarified; implementation not run

**Input**: Coordinator-assigned engineering amendment on merged `6971ba72cf5031ad50c4658606cc1d55c10242ac`: make the existing diagnostic-day artifact identities consistent from declaration through retrieval and verified acquisition replay. Planning owns only this feature's artifacts. Shared implementation starts only after verified 017 merge, latest-main integration and coordinator release of its sole shared-contract writer.

## Clarifications

### Session 2026-10-23

No critical ambiguities require human questions. Under the authorized workflow, engineering resolutions are recorded rather than presented as human answers:

- New probe records follow the already implemented retrieval identity convention; no alternate identifier system is introduced.
- Historical deployment-scoped client records and unassigned or parent-scoped repeated probe families remain readable but cannot establish acquisition proof. This compatibility allowance never changes their original identity.
- The prerequisite is verified 017 merge plus coordinator writer release and latest-main integration, not presumed completion of its current branch.
- The acceptance fixture proves identity and any genuinely supported closed intervals. It does not require fabricated full-day timing coverage from the existing abbreviated receiver fixture.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Replay one DAY experiment without ambiguous files (Priority: P1)

An operator needs each saved diagnostic-day file to identify the continuous TCP measurement or the particular probe invocation that produced it, so validated replay can preserve the real measurement windows and gaps.

**Why this priority**: A retrieved file cannot support timing coverage when its identity cannot identify one producing invocation.

**Independent Test**: A credential-free fixture runs the existing producer, resolves its declared files through the existing retrieval boundary, admits the resulting validation input and replays the verified raw evidence. Include at least two distinct probe chunks and one continuous TCP client.

**Acceptance Scenarios**:

1. **Given** one diagnostic-day measurement and several declared probe chunks, **When** closed files are retrieved and replayed, **Then** the continuous client belongs to that day and each chunk's files resolve uniquely to its own invocation within the same deployment.
2. **Given** a wrong chunk identifier, parent, producer, path or deployment, **When** evidence is admitted or replayed, **Then** it cannot support acquisition proof for another invocation.
3. **Given** a restart gap or failed final chunk, **When** valid earlier files are replayed, **Then** their supported intervals survive, the gap or unknown tail remains explicit and TCP is not restarted.

---

### User Story 2 - Read old bundles without inventing proof (Priority: P2)

A validator author needs historical V2 bundles to remain readable while missing or ambiguous DAY identity remains unavailable evidence.

**Why this priority**: A compatibility repair must not turn an old declaration or filename into evidence that was never recorded.

**Independent Test**: Read legacy DAY records with unassigned continuous-client units or repeated probe-family identities and compare them with modern records. Replay must retain unavailable acquisition proof for the legacy forms and leave original records and bytes unchanged.

**Acceptance Scenarios**:

1. **Given** a previously accepted DAY bundle using deployment-scoped client files or repeated probe-family identities attached to the day parent, **When** it is read, **Then** it remains readable with unavailable acquisition proof, without synthesized identities or rewritten records.
2. **Given** substituted raw bytes, ambiguous references or only claimed overlap, **When** proof is requested, **Then** existing verification prevents unsupported coverage while independently verified sources remain usable.
3. **Given** acceptance, short-study or calibration input, **When** the shared repair is applied, **Then** its existing identity rules and measurement protocol are preserved.

### Edge Cases

- The day unit has an identifier other than the usual example name.
- Multiple probe chunks share one retained deployment but have distinct invocations and file paths; their count follows actual production rather than a hardcoded full-day count.
- A chunk is undeclared, points to another parent, or has inconsistent identifier/path suffixes.
- A modern record is combined with a legacy declaration, or a legacy record is read against freshly derived declarations.
- A native or observation file is missing, corrupt, truncated or from another producer/session; no boundary is inferred from filenames or requested duration.
- A historical bundle lacks acquisition observations altogether; no new evidence or defaults are manufactured.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: New diagnostic-day continuous-client native and observation artifacts MUST identify the actual sole continuous-day measurement unit; persistent receiver artifacts MUST remain deployment scoped.
- **FR-002**: Every new probe-chunk native CSV, log and observation artifact MUST have a unique concrete identity and the producing probe-chunk unit. Its declared family, exact destination and chunk identifier MUST agree, and that chunk MUST belong to the declared day parent.
- **FR-003**: Declaration, closed-file retrieval, validation admission and acquisition replay MUST agree on the same identity. Existing command, invocation, native session, build, size/hash and clock proof MUST remain required; filename association alone MUST NOT establish acquisition evidence.
- **FR-004**: Historical V2 DAY records using unassigned continuous-client units or repeated probe-family identities with unassigned or day-parent units MUST remain readable with unavailable acquisition proof. Compatibility MUST NOT rewrite records, rename sources, generate observations or promote incomplete/ambiguous identities into proof.
- **FR-005**: Independently verified closed intervals and valid earlier chunks MUST remain usable when another chunk fails or an observation has an unknown tail; gaps and failures MUST remain explicit and original evidence MUST remain unchanged.
- **FR-006**: Preserve all four execution modes, native file paths/formats, commands, durations, statistical rules and research thresholds. This amendment MUST NOT add a build-observation input field, weaken acquisition verification or introduce new services, paid execution or research decisions.
- **FR-007**: Credential-free automated acceptance MUST exercise the actual declaration-to-retrieval-to-validation-to-replay chain, multiple chunks, wrong-identity and corruption cases, legacy unavailable-proof behavior and unaffected modes. Fake evidence MUST NOT be reported as real tool or cloud acceptance.
- **FR-008**: Shared implementation MUST wait for verified 017 merge, latest-main integration and coordinator release of the sole shared-contract writer; changes MUST stay within the declared identity contracts, generated canonical schemas if affected, focused tests and feature documentation. F08 consumption remains a separate owner.

### Key Entities *(include if feature involves data)*

- **Continuous-day unit**: The single uninterrupted TCP measurement on one retained deployment.
- **Probe-chunk unit**: One bounded probe invocation with its own files and a declared day parent.
- **Artifact family declaration**: An expected fixed destination or chunk-dependent destination; it does not prove that a file exists.
- **Concrete artifact record**: One closed file's actual identity, producer, unit, deployment, location and observed integrity evidence.
- **Acquisition source**: Existing references linking immutable native/observation/build bytes to one command or receiver session; declarations alone are not proof.
- **Legacy DAY record**: Previously readable identity metadata that lacks the unique unit association required for acquisition proof.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Every modern DAY file in the multi-chunk acceptance fixture resolves to exactly one intended producing unit at every boundary, without an identity ambiguity.
- **SC-002**: Every deliberate wrong-identity, ambiguous-reference or corrupt-source case in the acceptance matrix prevents unsupported acquisition proof while preserving valid independent evidence.
- **SC-003**: Every covered historical DAY form remains readable with unavailable acquisition proof; original files and serialized record identities remain unchanged during replay.
- **SC-004**: The focused offline suite preserves acceptance, short-study and calibration behavior; the shared-write prerequisite and exact validation revision are recorded before implementation is declared complete.

## Assumptions

- This is an ordinary engineering consistency correction within the approved continuous-day design, not a change to measurement methodology or formal advisor approval.
- The existing retrieval convention for concrete chunk identities is authoritative for new records. Its exact encoding belongs in the contract/plan, not a new identifier system.
- Old readable records are preserved as historical inputs, not repaired into new evidence. A fresh malformed or forged modern identity is rejected rather than treated as compatible merely because it resembles a filename.
- F08 may deserialize verified original build-sidecar bytes using existing build models and helpers; that consumer work is outside this amendment.
- Planning started at the assigned base while 017 owned shared writes. The coordinator subsequently reported its verified merge; quickstart records that update. This branch remains unintegrated during planning. Implementation and final checks use subsequently integrated latest main, without consuming unmerged work.
