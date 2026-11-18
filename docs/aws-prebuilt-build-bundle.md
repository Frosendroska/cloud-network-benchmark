# Optional AWS controller-prebuilt tool bundle

The default bootstrap remains the guest source build. AWS callers may explicitly select a local
bundle by supplying `prebuilt_bundle={"path": "...", "sha256": "<64 lowercase hex>"}` to
`BootstrapConfig`. The bundle is always both pinned tools for Ubuntu 24.04 x86-64; it cannot
replace source pins, patch pins, the `/var/lib/cnb/install` prefix, acquisition semantics, command
timeouts, or the existing readiness and cleanup contracts. An invalid or changed bundle fails
closed before VM creation or before each per-role upload. Bootstrap never falls back to source
builds after an opted-in bundle fails.

Create a bundle only from already completed offline build evidence and the resulting executable
files. `cloud_network_benchmark.build_bundle.build_bundle` takes the production `SourceBuild`
rows, maps each tool to its binary path and operational build-evidence JSON path, and writes a new
local archive. It performs no download, build, provider, or credential operation. The returned
`path`, `sha256`, and `size_bytes` are the explicit declaration inputs. The operational evidence
must bind the pinned source archive, packaged observation patch, successful fixed build argv,
Ubuntu 24.04 x86-64 compiler target, ELF/runtime evidence, binary digest, and acquisition marker.

On each VM, bootstrap transfers the archive once and runs the fixed guest installer/verifier.
That installer checks archive/member bounds, hashes, source and evidence pins, guest OS/architecture,
ELF interpreter and glibc requirements, runtime dependencies, actual tool banners/capabilities,
and Sockperf mode help. It then writes the unchanged `CNB_BUILD_V1` identity plus a separate
`CNB_PREBUILT_V1` origin proof. Both role proofs and the exact controller archive are retained as
closed raw artifacts and replayed by validation. `CNB_PREBUILT_V1` records origin; it does not
change the build identity or imply provider acceptance.

The test-only installer filesystem-root mapping is not an operational setting. It is generated
only when explicitly requested by unit tests. The emitted production installer uses the absolute
`/var/lib/cnb` paths and must be executed and checked on Ubuntu 24.04 x86-64 before acceptance.
