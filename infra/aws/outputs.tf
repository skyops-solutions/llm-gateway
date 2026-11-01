output "alb_dns_name" {
  value = aws_lb.this.dns_name
}

output "alerts_topic_arn" {
  value = aws_sns_topic.alerts.arn
}

output "provider_keys_secret_arn" {
  value = aws_secretsmanager_secret.provider_keys.arn
}
