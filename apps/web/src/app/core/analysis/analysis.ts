import {
  Injectable,
  inject,
} from '@angular/core';

import {
  HttpClient,
  HttpHeaders,
  HttpParams,
} from '@angular/common/http';

import {
  Observable,
  map,
} from 'rxjs';

import {
  Auth,
} from '../auth/auth';


export type AnalysisStatus =
  | 'pending'
  | 'preparing'
  | 'cloning'
  | 'analyzing'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'confirmed';


export type CommitPolicy =
  | 'validated'
  | 'latest';


export type AnalysisEventLevel =
  | 'info'
  | 'success'
  | 'warning'
  | 'error';


export interface AnalysisEnvironmentVariable {
  name: string;
  sensitive: boolean;
  valueCaptured: boolean;
}


export interface AnalysisComponent {
  id: number;

  name: string;
  componentType: string;
  rootPath: string;

  runtime: string | null;
  framework: string | null;
  packageManager: string | null;

  buildCommand: string | null;
  startCommand: string | null;

  detectedPort: number | null;

  deployable: boolean;

  dockerfilePath: string | null;
  helmChartPath: string | null;

  kubernetesPaths: string[];

  environmentVariables:
    AnalysisEnvironmentVariable[];

  confidence: number;

  configuration:
    Record<string, unknown>;

  userModified: boolean;
}


export interface AnalysisInventory {
  fileCount: number;
  totalSizeBytes: number;
  ignoredFileCount: number;
  limitReached: boolean;
}


export interface AnalysisArgoCdSummary {
  existingApplications: string[];
  existingApplicationCount: number;

  appProjectManagedByEnvironment: boolean;

  applicationCreationPhase: number;

  confirmationRequired: boolean;
}


export interface AnalysisSummary {
  analysisRoot?: string;

  inventory?: AnalysisInventory;

  componentCount?: number;

  deployableComponentCount?: number;

  dockerfiles?: string[];

  helmCharts?: string[];

  kubernetesManifests?: string[];

  gitlabCiFiles?: string[];

  composeFiles?: string[];

  argoCd?: AnalysisArgoCdSummary;

  warnings?: string[];

  phase3Ready?: boolean;
}


export interface AnalysisError {
  code: string;
  message: string;
}


export interface ProjectAnalysis {
  id: number;
  projectId: number;

  commitPolicy: CommitPolicy;

  requestedCommitSha: string | null;
  branchHeadSha: string | null;
  analyzedCommitSha: string | null;

  selectedSubdirectory: string | null;

  status: AnalysisStatus;

  progress: number;

  currentStep: string;

  summary: AnalysisSummary;

  error: AnalysisError | null;

  components: AnalysisComponent[];

  createdAt: string | null;
  startedAt: string | null;
  finishedAt: string | null;
  confirmedAt: string | null;
}


export interface ProjectAnalysisEvent {
  id: number;

  level: AnalysisEventLevel;

  step: string;

  message: string;

  details:
    Record<string, unknown>;

  createdAt: string | null;
}


export interface UpdateAnalysisComponentRequest {
  name?: string;

  componentType?: string;

  runtime?: string | null;

  framework?: string | null;

  packageManager?: string | null;

  buildCommand?: string | null;

  startCommand?: string | null;

  detectedPort?: number | null;

  deployable?: boolean;
}


interface AnalysisResponse {
  success: boolean;

  data: {
    analysis: ProjectAnalysis;
  };
}


interface AnalysisEventsResponse {
  success: boolean;

  data: {
    events: ProjectAnalysisEvent[];
  };
}


interface AnalysisComponentResponse {
  success: boolean;

  data: {
    component: AnalysisComponent;
  };
}


interface ConfirmAnalysisResponse {
  success: boolean;

  data: {
    confirmed: boolean;
  };
}


@Injectable({
  providedIn: 'root',
})
export class AnalysisService {
  private readonly http =
    inject(HttpClient);

  private readonly auth =
    inject(Auth);


  startAnalysis(
    projectId: number,
    commitPolicy: CommitPolicy,
  ): Observable<ProjectAnalysis> {
    return this.http
      .post<AnalysisResponse>(
        `/api/projects/${projectId}/analyses`,
        {
          commitPolicy,
        },
        {
          headers: this.createHeaders(),
        },
      )
      .pipe(
        map(
          response =>
            response.data.analysis,
        ),
      );
  }


  getLatestAnalysis(
    projectId: number,
  ): Observable<ProjectAnalysis> {
    return this.http
      .get<AnalysisResponse>(
        (
          `/api/projects/${projectId}`
          + '/analyses/latest'
        ),
        {
          headers: this.createHeaders(),
        },
      )
      .pipe(
        map(
          response =>
            response.data.analysis,
        ),
      );
  }


  getAnalysis(
    projectId: number,
    analysisId: number,
  ): Observable<ProjectAnalysis> {
    return this.http
      .get<AnalysisResponse>(
        (
          `/api/projects/${projectId}`
          + `/analyses/${analysisId}`
        ),
        {
          headers: this.createHeaders(),
        },
      )
      .pipe(
        map(
          response =>
            response.data.analysis,
        ),
      );
  }


  getEvents(
    projectId: number,
    analysisId: number,
    afterId = 0,
  ): Observable<ProjectAnalysisEvent[]> {
    const params =
      new HttpParams().set(
        'afterId',
        afterId,
      );

    return this.http
      .get<AnalysisEventsResponse>(
        (
          `/api/projects/${projectId}`
          + `/analyses/${analysisId}`
          + '/events'
        ),
        {
          headers: this.createHeaders(),
          params,
        },
      )
      .pipe(
        map(
          response =>
            response.data.events,
        ),
      );
  }


  updateComponent(
    projectId: number,
    analysisId: number,
    componentId: number,
    request:
      UpdateAnalysisComponentRequest,
  ): Observable<AnalysisComponent> {
    return this.http
      .patch<AnalysisComponentResponse>(
        (
          `/api/projects/${projectId}`
          + `/analyses/${analysisId}`
          + `/components/${componentId}`
        ),
        request,
        {
          headers: this.createHeaders(),
        },
      )
      .pipe(
        map(
          response =>
            response.data.component,
        ),
      );
  }


  confirmAnalysis(
    projectId: number,
    analysisId: number,
  ): Observable<boolean> {
    return this.http
      .post<ConfirmAnalysisResponse>(
        (
          `/api/projects/${projectId}`
          + `/analyses/${analysisId}`
          + '/confirm'
        ),
        {},
        {
          headers: this.createHeaders(),
        },
      )
      .pipe(
        map(
          response =>
            response.data.confirmed,
        ),
      );
  }


  private createHeaders():
    HttpHeaders {
    const accessToken =
      this.auth.getAccessToken();

    if (!accessToken) {
      return new HttpHeaders();
    }

    return new HttpHeaders({
      Authorization:
        `Bearer ${accessToken}`,
    });
  }
}