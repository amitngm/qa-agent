{{/*
Expand the name of the chart.
*/}}
{{- define "testbuddy.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "testbuddy.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Chart label
*/}}
{{- define "testbuddy.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "testbuddy.labels" -}}
helm.sh/chart: {{ include "testbuddy.chart" . }}
{{ include "testbuddy.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "testbuddy.selectorLabels" -}}
app.kubernetes.io/name: {{ include "testbuddy.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
ServiceAccount name
*/}}
{{- define "testbuddy.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "testbuddy.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Secret name for buddy credentials
*/}}
{{- define "testbuddy.secretName" -}}
{{- include "testbuddy.fullname" . }}-secrets
{{- end }}

{{/*
ConfigMap name
*/}}
{{- define "testbuddy.configMapName" -}}
{{- include "testbuddy.fullname" . }}-config
{{- end }}

{{/*
PVC name
*/}}
{{- define "testbuddy.pvcName" -}}
{{- include "testbuddy.fullname" . }}-data
{{- end }}
