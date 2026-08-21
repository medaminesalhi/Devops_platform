import { HttpClient, HttpHeaders, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable, delay, map, of, throwError } from 'rxjs';

import { Auth } from '../auth/auth';

/**
 * Le backend Performance sera ajouté à l'étape suivante.
 * Laissez true pour valider entièrement le frontend avec des données locales.
 * Passez à false dès que les routes Flask /api/performance sont disponibles.
 */
export const PERFORMANCE_DEMO_MODE = true;

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
  namespace: string;
  retentionDays: number;
  grafanaIngressHost: string | null;
  installPrometheus: boolean;
  installGrafana: boolean;
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
  logs: PerformanceRunLog[];
  errorCode: string | null;
  errorMessage: string | null;
}

export interface PerformanceRunLog {
  id: number;
  createdAt: string;
  level: 'info' | 'success' | 'warning' | 'error';
  message: string;
}

export interface CreatePerformanceTestRequest {
  projectId: number;
  deploymentId: number | null;
  projectName?: string;
  name: string;
  description: string | null;
  targetUrl: string;
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

  private nextTestId = 40;
  private nextRunId = 130;
  private readonly tests = this.createMockTests();
  private readonly runs = this.createMockRuns();

  getOverview(): Observable<PerformanceOverview> {
    if (!PERFORMANCE_DEMO_MODE) {
      return this.http
        .get<ApiResponse<PerformanceOverview>>('/api/performance/overview', { headers: this.headers() })
        .pipe(map(response => response.data));
    }

    return this.respond(this.buildOverview());
  }

  listTests(filters: PerformanceListFilters = {}): Observable<PerformanceTest[]> {
    if (!PERFORMANCE_DEMO_MODE) {
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

    let items = [...this.tests];
    const search = filters.search?.trim().toLowerCase();
    if (search) {
      items = items.filter(item =>
        item.name.toLowerCase().includes(search)
        || item.projectName.toLowerCase().includes(search)
        || item.targetUrl.toLowerCase().includes(search),
      );
    }
    if (filters.mode) items = items.filter(item => item.mode === filters.mode);
    return this.respond(items.sort((a, b) => b.updatedAt.localeCompare(a.updatedAt)));
  }

  listRuns(filters: PerformanceListFilters = {}): Observable<PerformanceRunSummary[]> {
    if (!PERFORMANCE_DEMO_MODE) {
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

    let items: PerformanceRunSummary[] = [...this.runs];
    if (filters.status) items = items.filter(item => item.status === filters.status);
    if (filters.mode) items = items.filter(item => item.mode === filters.mode);
    return this.respond(items.sort((a, b) => b.createdAt.localeCompare(a.createdAt)));
  }

  getRun(runId: number): Observable<PerformanceRun> {
    if (!PERFORMANCE_DEMO_MODE) {
      return this.http
        .get<ApiResponse<{ run: PerformanceRun }>>(`/api/performance/runs/${runId}`, {
          headers: this.headers(),
        })
        .pipe(map(response => response.data.run));
    }

    const run = this.runs.find(item => item.id === runId);
    return run
      ? this.respond(this.cloneRun(run))
      : throwError(() => new Error('Test de performance introuvable.'));
  }

  createAndRun(request: CreatePerformanceTestRequest): Observable<PerformanceRun> {
    if (!PERFORMANCE_DEMO_MODE) {
      return this.http
        .post<ApiResponse<{ run: PerformanceRun }>>(
          '/api/performance/tests/run',
          request,
          { headers: this.headers() },
        )
        .pipe(map(response => response.data.run));
    }

    const now = new Date().toISOString();
    const testId = this.nextTestId++;
    const runId = this.nextRunId++;
    const projectName = request.projectName?.trim() || `Projet #${request.projectId}`;

    const run: PerformanceRun = {
      id: runId,
      testId,
      testName: request.name,
      projectId: request.projectId,
      projectName,
      deploymentId: request.deploymentId,
      mode: request.mode,
      testType: request.testType,
      status: 'queued',
      targetUrl: request.targetUrl,
      createdAt: now,
      startedAt: null,
      finishedAt: null,
      durationSeconds: request.loadProfile.durationSeconds,
      maxVirtualUsers: request.loadProfile.maxVirtualUsers,
      metrics: null,
      grafanaDashboardUrl: null,
      thresholds: this.emptyThresholdResults(request.thresholds),
      observability: request.observability,
      logs: [{ id: 1, createdAt: now, level: 'info', message: 'Test créé et ajouté à la file du worker k6.' }],
      errorCode: null,
      errorMessage: null,
    };

    const test: PerformanceTest = {
      id: testId,
      projectId: request.projectId,
      projectName,
      deploymentId: request.deploymentId,
      name: request.name,
      description: request.description,
      targetUrl: request.targetUrl,
      testType: request.testType,
      mode: request.mode,
      loadProfile: request.loadProfile,
      thresholds: request.thresholds,
      observability: request.observability,
      createdAt: now,
      updatedAt: now,
      lastRun: run,
    };

    this.tests.unshift(test);
    this.runs.unshift(run);
    return this.respond(this.cloneRun(run), 500);
  }

  cancelRun(runId: number): Observable<PerformanceRun> {
    if (!PERFORMANCE_DEMO_MODE) {
      return this.http
        .post<ApiResponse<{ run: PerformanceRun }>>(
          `/api/performance/runs/${runId}/cancel`,
          {},
          { headers: this.headers() },
        )
        .pipe(map(response => response.data.run));
    }

    const run = this.runs.find(item => item.id === runId);
    if (!run) return throwError(() => new Error('Test de performance introuvable.'));
    run.status = 'cancelled';
    run.finishedAt = new Date().toISOString();
    run.logs.push({
      id: run.logs.length + 1,
      createdAt: run.finishedAt,
      level: 'warning',
      message: 'Exécution annulée par l’utilisateur.',
    });
    return this.respond(this.cloneRun(run));
  }

  private buildOverview(): PerformanceOverview {
    return {
      totalTests: this.tests.length,
      totalRuns: this.runs.length,
      runningRuns: this.runs.filter(item => item.status === 'queued' || item.status === 'running').length,
      passedRuns: this.runs.filter(item => item.status === 'passed').length,
      failedRuns: this.runs.filter(item => item.status === 'failed').length,
    };
  }

  private emptyThresholdResults(thresholds: PerformanceThresholds): PerformanceThresholdResult[] {
    return [
      { key: 'error_rate', label: 'Taux d’erreur', expected: `< ${thresholds.errorRatePercent} %`, actual: 'En attente', passed: false },
      { key: 'p95', label: 'Latence p95', expected: `< ${thresholds.p95Ms} ms`, actual: 'En attente', passed: false },
      { key: 'p99', label: 'Latence p99', expected: `< ${thresholds.p99Ms} ms`, actual: 'En attente', passed: false },
      { key: 'checks', label: 'Checks réussis', expected: `> ${thresholds.checksRatePercent} %`, actual: 'En attente', passed: false },
    ];
  }

  private createMockTests(): PerformanceTest[] {
    return [
      {
        id: 31,
        projectId: 3,
        projectName: 'sapixi-platform',
        deploymentId: 52,
        name: 'Smoke après déploiement',
        description: 'Validation rapide de l’API après publication.',
        targetUrl: 'https://sapixi-platform.lab.local',
        testType: 'smoke',
        mode: 'basic',
        loadProfile: { virtualUsers: 2, maxVirtualUsers: 2, durationSeconds: 30 },
        thresholds: { errorRatePercent: 1, p95Ms: 500, p99Ms: 1000, checksRatePercent: 99 },
        observability: null,
        createdAt: '2026-08-21T15:10:00Z',
        updatedAt: '2026-08-21T16:20:00Z',
        lastRun: null,
      },
      {
        id: 32,
        projectId: 4,
        projectName: 'orders-api',
        deploymentId: 47,
        name: 'Charge API commandes',
        description: 'Charge moyenne avec observabilité Prometheus/Grafana.',
        targetUrl: 'https://orders.test.local',
        testType: 'load',
        mode: 'observability',
        loadProfile: { virtualUsers: 25, maxVirtualUsers: 100, durationSeconds: 300 },
        thresholds: { errorRatePercent: 1, p95Ms: 450, p99Ms: 900, checksRatePercent: 99 },
        observability: {
          namespace: 'performance-observability',
          retentionDays: 7,
          grafanaIngressHost: 'grafana.test.local',
          installPrometheus: true,
          installGrafana: true,
        },
        createdAt: '2026-08-20T11:00:00Z',
        updatedAt: '2026-08-21T14:05:00Z',
        lastRun: null,
      },
    ];
  }

  private createMockRuns(): PerformanceRun[] {
    const basicMetrics: PerformanceMetrics = {
      requests: 18420,
      rps: 612.4,
      avgMs: 176,
      minMs: 31,
      maxMs: 921,
      p90Ms: 268,
      p95Ms: 342,
      p99Ms: 704,
      errorRatePercent: 0.28,
      checksRatePercent: 99.84,
      dataReceivedBytes: 148_500_000,
      dataSentBytes: 12_300_000,
      iterations: 18120,
    };

    const advancedMetrics: PerformanceMetrics = {
      requests: 142530,
      rps: 475.1,
      avgMs: 212,
      minMs: 38,
      maxMs: 2311,
      p90Ms: 356,
      p95Ms: 438,
      p99Ms: 823,
      errorRatePercent: 0.41,
      checksRatePercent: 99.52,
      dataReceivedBytes: 1_140_000_000,
      dataSentBytes: 92_000_000,
      iterations: 140220,
    };

    const failedMetrics: PerformanceMetrics = {
      requests: 96500,
      rps: 321.6,
      avgMs: 624,
      minMs: 42,
      maxMs: 5420,
      p90Ms: 980,
      p95Ms: 1320,
      p99Ms: 2880,
      errorRatePercent: 3.7,
      checksRatePercent: 96.1,
      dataReceivedBytes: 680_000_000,
      dataSentBytes: 61_000_000,
      iterations: 92410,
    };

    return [
      {
        id: 126,
        testId: 31,
        testName: 'Smoke après déploiement',
        projectId: 3,
        projectName: 'sapixi-platform',
        deploymentId: 52,
        mode: 'basic',
        testType: 'smoke',
        status: 'passed',
        targetUrl: 'https://sapixi-platform.lab.local',
        createdAt: '2026-08-21T16:20:00Z',
        startedAt: '2026-08-21T16:20:02Z',
        finishedAt: '2026-08-21T16:20:32Z',
        durationSeconds: 30,
        maxVirtualUsers: 2,
        metrics: basicMetrics,
        grafanaDashboardUrl: null,
        thresholds: [
          { key: 'error_rate', label: 'Taux d’erreur', expected: '< 1 %', actual: '0,28 %', passed: true },
          { key: 'p95', label: 'Latence p95', expected: '< 500 ms', actual: '342 ms', passed: true },
          { key: 'p99', label: 'Latence p99', expected: '< 1000 ms', actual: '704 ms', passed: true },
          { key: 'checks', label: 'Checks réussis', expected: '> 99 %', actual: '99,84 %', passed: true },
        ],
        observability: null,
        logs: [
          { id: 1, createdAt: '2026-08-21T16:20:00Z', level: 'info', message: 'Run créé depuis le déploiement #52.' },
          { id: 2, createdAt: '2026-08-21T16:20:02Z', level: 'info', message: 'Worker k6 affecté. Préparation du script.' },
          { id: 3, createdAt: '2026-08-21T16:20:03Z', level: 'info', message: 'k6 démarré avec 2 VUs pour 30 secondes.' },
          { id: 4, createdAt: '2026-08-21T16:20:32Z', level: 'success', message: 'Tous les seuils de performance sont respectés.' },
        ],
        errorCode: null,
        errorMessage: null,
      },
      {
        id: 125,
        testId: 32,
        testName: 'Charge API commandes',
        projectId: 4,
        projectName: 'orders-api',
        deploymentId: 47,
        mode: 'observability',
        testType: 'load',
        status: 'passed',
        targetUrl: 'https://orders.test.local',
        createdAt: '2026-08-21T14:00:00Z',
        startedAt: '2026-08-21T14:00:05Z',
        finishedAt: '2026-08-21T14:05:05Z',
        durationSeconds: 300,
        maxVirtualUsers: 100,
        metrics: advancedMetrics,
        grafanaDashboardUrl: 'https://grafana.example.local/d/k6-performance?var-testid=125',
        thresholds: [
          { key: 'error_rate', label: 'Taux d’erreur', expected: '< 1 %', actual: '0,41 %', passed: true },
          { key: 'p95', label: 'Latence p95', expected: '< 450 ms', actual: '438 ms', passed: true },
          { key: 'p99', label: 'Latence p99', expected: '< 900 ms', actual: '823 ms', passed: true },
          { key: 'checks', label: 'Checks réussis', expected: '> 99 %', actual: '99,52 %', passed: true },
        ],
        observability: {
          namespace: 'performance-observability',
          retentionDays: 7,
          grafanaIngressHost: 'grafana.test.local',
          installPrometheus: true,
          installGrafana: true,
        },
        logs: [
          { id: 1, createdAt: '2026-08-21T14:00:00Z', level: 'info', message: 'Run créé en mode Observabilité.' },
          { id: 2, createdAt: '2026-08-21T14:00:01Z', level: 'success', message: 'Namespace performance-observability validé.' },
          { id: 3, createdAt: '2026-08-21T14:00:03Z', level: 'success', message: 'Prometheus et Grafana disponibles.' },
          { id: 4, createdAt: '2026-08-21T14:00:05Z', level: 'info', message: 'k6 démarré avec Remote Write Prometheus.' },
          { id: 5, createdAt: '2026-08-21T14:05:05Z', level: 'success', message: 'Test terminé et dashboard Grafana associé.' },
        ],
        errorCode: null,
        errorMessage: null,
      },
      {
        id: 124,
        testId: 32,
        testName: 'Charge API commandes',
        projectId: 4,
        projectName: 'orders-api',
        deploymentId: 46,
        mode: 'observability',
        testType: 'stress',
        status: 'failed',
        targetUrl: 'https://orders.test.local',
        createdAt: '2026-08-20T16:00:00Z',
        startedAt: '2026-08-20T16:00:04Z',
        finishedAt: '2026-08-20T16:05:04Z',
        durationSeconds: 300,
        maxVirtualUsers: 250,
        metrics: failedMetrics,
        grafanaDashboardUrl: 'https://grafana.example.local/d/k6-performance?var-testid=124',
        thresholds: [
          { key: 'error_rate', label: 'Taux d’erreur', expected: '< 1 %', actual: '3,70 %', passed: false },
          { key: 'p95', label: 'Latence p95', expected: '< 500 ms', actual: '1320 ms', passed: false },
          { key: 'p99', label: 'Latence p99', expected: '< 1000 ms', actual: '2880 ms', passed: false },
          { key: 'checks', label: 'Checks réussis', expected: '> 99 %', actual: '96,10 %', passed: false },
        ],
        observability: {
          namespace: 'performance-observability',
          retentionDays: 7,
          grafanaIngressHost: 'grafana.test.local',
          installPrometheus: true,
          installGrafana: true,
        },
        logs: [
          { id: 1, createdAt: '2026-08-20T16:00:00Z', level: 'info', message: 'Stress test préparé.' },
          { id: 2, createdAt: '2026-08-20T16:02:42Z', level: 'warning', message: 'Le p95 dépasse le seuil de 500 ms.' },
          { id: 3, createdAt: '2026-08-20T16:03:18Z', level: 'warning', message: 'Le taux d’erreur dépasse 1 %.' },
          { id: 4, createdAt: '2026-08-20T16:05:04Z', level: 'error', message: 'Le quality gate de performance a échoué.' },
        ],
        errorCode: 'THRESHOLD_FAILED',
        errorMessage: 'Un ou plusieurs thresholds k6 ne sont pas respectés.',
      },
    ];
  }

  private cloneRun(run: PerformanceRun): PerformanceRun {
    return JSON.parse(JSON.stringify(run)) as PerformanceRun;
  }

  private respond<T>(data: T, milliseconds = 180): Observable<T> {
    return of(data).pipe(delay(milliseconds));
  }

  private headers(): HttpHeaders {
    const token = this.auth.getAccessToken();
    return token
      ? new HttpHeaders({ Authorization: `Bearer ${token}` })
      : new HttpHeaders();
  }
}
