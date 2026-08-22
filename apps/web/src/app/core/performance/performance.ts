import { HttpClient, HttpHeaders, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable, map } from 'rxjs';

import { Auth } from '../auth/auth';

export type PerformanceMode = 'basic' | 'observability';
export type PerformanceTestType = 'smoke' | 'load' | 'stress' | 'spike' | 'soak' | 'custom';
export type PerformanceRunStatus = 'queued' | 'running' | 'passed' | 'failed' | 'cancelled';

export interface PerformanceThresholds {
  errorRatePercent: number;
  p95Ms: number;
  p99Ms: number;
  checksRatePercent: number;
}

export interface PerformanceLoadProfile {
  virtualUsers: number;
  maxVirtualUsers: number;
  durationSeconds: number;
}

export interface PerformanceObservabilityConfig {
  namespace: string | null;
  retentionDays: number;
  prometheusRemoteWriteUrl: string;
  grafanaBaseUrl: string | null;
  grafanaDashboardUid: string;
}

export interface PerformanceTest {
  id: number;
  projectId: number;
  projectName: string;
  deploymentId: number | null;
  name: string;
  description: string | null;
  targetUrl: string;
  testType: PerformanceTestType;
  mode: PerformanceMode;
  loadProfile: PerformanceLoadProfile;
  thresholds: PerformanceThresholds;
  observability: PerformanceObservabilityConfig | null;
  createdAt: string;
  updatedAt: string;
  lastRun: PerformanceRunSummary | null;
}

export interface PerformanceMetrics {
  requests: number;
  rps: number;
  avgMs: number;
  minMs: number;
  maxMs: number;
  p90Ms: number;
  p95Ms: number;
  p99Ms: number;
  errorRatePercent: number;
  checksRatePercent: number;
  dataReceivedBytes: number;
  dataSentBytes: number;
  iterations: number;
}

export interface PerformanceThresholdResult {
  key: 'error_rate' | 'p95' | 'p99' | 'checks';
  label: string;
  expected: string;
  actual: string;
  passed: boolean;
}

export interface PerformanceRunSummary {
  id: number;
  testId: number;
  testName: string;
  projectId: number;
  projectName: string;
  deploymentId: number | null;
  mode: PerformanceMode;
  testType: PerformanceTestType;
  status: PerformanceRunStatus;
  targetUrl: string;
  createdAt: string;
  startedAt: string | null;
  finishedAt: string | null;
  durationSeconds: number;
  maxVirtualUsers: number;
  metrics: PerformanceMetrics | null;
  grafanaDashboardUrl: string | null;
}

export interface PerformanceRun extends PerformanceRunSummary {
  thresholds: PerformanceThresholdResult[];
  observability: PerformanceObservabilityConfig | null;
  samples: PerformanceSample[];
  logs: PerformanceRunLog[];
  errorCode: string | null;
  errorMessage: string | null;
}

export interface PerformanceSample {
  id: number;
  sampledAt: string;
  elapsedSeconds: number;
  vus: number;
  requests: number;
  requestsTotal: number;
  iterationsTotal: number;
  rps: number;
  avgMs: number;
  p95Ms: number;
  p99Ms: number;
  errorRatePercent: number;
  checksRatePercent: number;
}

export interface PerformanceRunLog {
  id: number;
  createdAt: string;
  level: 'info' | 'success' | 'warning' | 'error';
  message: string;
}

export interface PerformanceRuntimeConfig {
  limits: {
    maxVirtualUsers: number;
    maxDurationSeconds: number;
    maxRetentionDays: number;
  };
  targetPolicy: {
    configuredFromInterface: boolean;
    allowlistRequired: boolean;
    authorizationConfirmationRequired: boolean;
  };
  observability: {
    configuredFromInterface: boolean;
    prometheusRemoteWriteUrlRequired: boolean;
    grafanaBaseUrlRequired: boolean;
  };
}

export interface CreatePerformanceTestRequest {
  projectId: number;
  deploymentId: number | null;
  name: string;
  description: string | null;
  targetUrl: string;
  authorizationConfirmed: boolean;
  testType: PerformanceTestType;
  mode: PerformanceMode;
  loadProfile: PerformanceLoadProfile;
  thresholds: PerformanceThresholds;
  observability: PerformanceObservabilityConfig | null;
}

export interface PerformanceListFilters {
  search?: string | null;
  mode?: PerformanceMode | null;
  status?: PerformanceRunStatus | null;
}

export interface PerformanceOverview {
  totalTests: number;
  totalRuns: number;
  runningRuns: number;
  passedRuns: number;
  failedRuns: number;
}

interface ApiResponse<T> {
  success: boolean;
  data: T;
}

@Injectable({ providedIn: 'root' })
export class PerformanceService {
  private readonly http = inject(HttpClient);
  private readonly auth = inject(Auth);

  getConfig(): Observable<PerformanceRuntimeConfig> {
    return this.http
      .get<ApiResponse<PerformanceRuntimeConfig>>('/api/performance/config', {
        headers: this.headers(),
      })
      .pipe(map(response => response.data));
  }

  getOverview(): Observable<PerformanceOverview> {
    return this.http
      .get<ApiResponse<PerformanceOverview>>('/api/performance/overview', {
        headers: this.headers(),
      })
      .pipe(map(response => response.data));
  }

  listTests(filters: PerformanceListFilters = {}): Observable<PerformanceTest[]> {
    let params = new HttpParams();
    if (filters.search?.trim()) params = params.set('search', filters.search.trim());
    if (filters.mode) params = params.set('mode', filters.mode);

    return this.http
      .get<ApiResponse<{ tests: PerformanceTest[] }>>('/api/performance/tests', {
        headers: this.headers(),
        params,
      })
      .pipe(map(response => response.data.tests));
  }

  listRuns(filters: PerformanceListFilters = {}): Observable<PerformanceRunSummary[]> {
    let params = new HttpParams();
    if (filters.status) params = params.set('status', filters.status);
    if (filters.mode) params = params.set('mode', filters.mode);

    return this.http
      .get<ApiResponse<{ runs: PerformanceRunSummary[] }>>('/api/performance/runs', {
        headers: this.headers(),
        params,
      })
      .pipe(map(response => response.data.runs));
  }

  getRun(runId: number): Observable<PerformanceRun> {
    return this.http
      .get<ApiResponse<{ run: PerformanceRun }>>(`/api/performance/runs/${runId}`, {
        headers: this.headers(),
      })
      .pipe(map(response => response.data.run));
  }

  createAndRun(request: CreatePerformanceTestRequest): Observable<PerformanceRun> {
    return this.http
      .post<ApiResponse<{ run: PerformanceRun }>>(
        '/api/performance/tests/run',
        request,
        { headers: this.headers() },
      )
      .pipe(map(response => response.data.run));
  }

  cancelRun(runId: number): Observable<PerformanceRun> {
    return this.http
      .post<ApiResponse<{ run: PerformanceRun }>>(
        `/api/performance/runs/${runId}/cancel`,
        {},
        { headers: this.headers() },
      )
      .pipe(map(response => response.data.run));
  }

  rerunRun(runId: number): Observable<PerformanceRun> {
    return this.http
      .post<ApiResponse<{ run: PerformanceRun }>>(
        `/api/performance/runs/${runId}/rerun`,
        {},
        { headers: this.headers() },
      )
      .pipe(map(response => response.data.run));
  }

  private headers(): HttpHeaders {
    const token = this.auth.getAccessToken();
    return token
      ? new HttpHeaders({ Authorization: `Bearer ${token}` })
      : new HttpHeaders();
  }
}
