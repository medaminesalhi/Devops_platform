{{- define "sapixi.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "sapixi.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name (include "sapixi.name" .) | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}

{{- define "sapixi.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | quote }}
app.kubernetes.io/name: {{ include "sapixi.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
sapixi.io/managed: "true"
{{- end }}

{{- define "sapixi.selectorLabels" -}}
app.kubernetes.io/name: {{ include "sapixi.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}
