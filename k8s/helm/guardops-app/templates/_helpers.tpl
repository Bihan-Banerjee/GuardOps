{{/*
_helpers.tpl — Reusable template snippets for the guardops-app chart.

WHY helpers exist:
  Helm templates repeat the same labels, selectors, and name logic across
  every manifest. Helpers let us define it once and reuse it everywhere.
  If the project name changes, only this file needs updating.
*/}}

{{/*
Expand the name of the chart.
Uses .Values.app.name if set, falls back to .Chart.Name.
*/}}
{{- define "guardops-app.name" -}}
{{- .Values.app.name | default .Chart.Name | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Full release name: combines release name + chart name, truncated to 63 chars.
Kubernetes names must be <= 63 characters (DNS label limit).
*/}}
{{- define "guardops-app.fullname" -}}
{{- $name := .Values.app.name | default .Chart.Name }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}

{{/*
Chart label: used to track which chart version deployed this resource.
*/}}
{{- define "guardops-app.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels applied to every resource.
These are used by Helm for upgrade/rollback tracking.
*/}}
{{- define "guardops-app.labels" -}}
helm.sh/chart: {{ include "guardops-app.chart" . }}
{{ include "guardops-app.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
managed-by: guardops
{{- end }}

{{/*
Selector labels — used by Deployment selector and Service selector.
Must be STABLE across upgrades (changing these breaks rolling updates).
*/}}
{{- define "guardops-app.selectorLabels" -}}
app.kubernetes.io/name: {{ include "guardops-app.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Full image reference: repository:tag
*/}}
{{- define "guardops-app.image" -}}
{{- printf "%s:%s" .Values.image.repository (.Values.image.tag | default "latest") }}
{{- end }}
