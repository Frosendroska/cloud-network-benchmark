# Terraform

Provider-specific Terraform will live here.

Current intended layout:

```text
terraform/
├── aws/
├── azure/
└── gcp/
```

Keep provider directories explicit unless implementation proves a shared abstraction is necessary.

Do not commit Terraform state, secret variables, cloud credentials, SSH keys, or generated plans.
