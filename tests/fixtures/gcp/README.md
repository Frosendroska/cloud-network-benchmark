# GCP offline fixtures

These are synthetic, credential-free provider fixtures. They are not API observations or price quotes. The support-cost note records the planning assumption that instance templates and compact placement policies have no standalone charge; the complete reservation must separately price compute, disks, addresses, network transfer, tax, and uncertainty from dated evidence. Refresh and hash the provider source before paid admission.

Synthetic immutable image identifiers in tests use the `ubuntu-2404-lts-amd64-v...` name pattern under `projects/ubuntu-os-cloud/global/images/`. They are placeholders only: they do not claim that a dated image exists in Frankfurt or London. A real immutable image identity and its regional availability must be selected during the separate controlled acceptance stage; family aliases are rejected by the runtime settings.

The support-cost capture does not supply a complete VM reservation quote. Before admission, the caller must provide dated source/hash-bound compute hours, boot-disk GiB-hours, public-address hours, network-egress GiB, taxes and uncertainty as separate inputs. The fixtures deliberately do not invent those prices.

`scenarios.json` and `operation-traces.json` are planning-aligned expected shapes/orderings only. All image references and API-response identities in tests are synthetic; they cannot establish zone availability, image contents, provider inheritance, actual VM identity, or successful cleanup.
