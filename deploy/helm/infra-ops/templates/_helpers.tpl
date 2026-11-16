{{- define "infraops.name" -}}{{ .Release.Name }}{{- end }}
{{- define "infraops.labels" -}}
app.kubernetes.io/part-of: infra-ops
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
{{- define "infraops.env" -}}
{{- range $k, $v := .Values.env }}
- name: {{ $k }}
  value: {{ $v | quote }}
{{- end }}
- name: INFRAOPS_LOG_JSON
  value: "true"
{{- if .Values.internalCaConfigMap }}
- name: SSL_CERT_FILE
  value: /etc/infraops/ca/ca-bundle.crt
{{- end }}
{{- end }}
{{- define "infraops.podSecurity" -}}
securityContext:
  runAsNonRoot: true
  allowPrivilegeEscalation: false
  readOnlyRootFilesystem: true
  capabilities: { drop: ["ALL"] }
{{- end }}
