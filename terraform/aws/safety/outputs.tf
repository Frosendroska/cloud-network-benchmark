output "document_name" {
  value = aws_ssm_document.expiry.name
}

output "document_version" {
  value = aws_ssm_document.expiry.default_version
}

output "document_hash" {
  value = aws_ssm_document.expiry.hash
}

output "role_arn" {
  value = aws_iam_role.expiry.arn
}
