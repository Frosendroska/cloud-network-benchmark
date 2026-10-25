# F09 synthetic fixtures

The helpers produce deterministic controller and guest clocks and stage traces. Every
event is marked synthetic by the fake callbacks; these fixtures do not call a cloud,
SSH, benchmark binary, Terraform, or a real runner. Native-result and observation
fixtures are sourced from the existing F03/F04/F08 test producers when the bundle
tests exercise those production boundaries; they are not copied into this directory
as fabricated proof.
