# GCP offline fixtures

These are synthetic, credential-free provider fixtures. They are not API observations or price quotes. The support-cost note records the planning assumption that instance templates and compact placement policies have no standalone charge; the complete reservation must separately price compute, disks, addresses, network transfer, tax, and uncertainty from dated evidence. Refresh and hash the provider source before paid admission.

Synthetic image references in tests use the `ubuntu-os-cloud/ubuntu-2404-lts-amd64` family and must be resolved to immutable image identities during the separate controlled acceptance stage.

`scenarios.json` and `operation-traces.json` are planning-aligned expected shapes/orderings only. All image references and API-response identities in tests are synthetic; they cannot establish zone availability, image contents, provider inheritance, actual VM identity, or successful cleanup.
