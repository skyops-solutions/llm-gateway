{{- define "gw.name" -}}{{ .Release.Name }}-llm-gateway{{- end -}}
{{- define "gw.labels" -}}
app.kubernetes.io/name: llm-gateway
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end -}}
{{- define "gw.selector" -}}
app.kubernetes.io/name: llm-gateway
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}
{{- define "gw.redisUrl" -}}
{{- if .Values.redis.enabled -}}redis://{{ .Release.Name }}-redis:6379/0{{- else -}}{{ required "redis.url is required when redis.enabled=false" .Values.redis.url }}{{- end -}}
{{- end -}}
