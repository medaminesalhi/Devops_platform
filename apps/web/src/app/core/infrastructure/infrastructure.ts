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


export type EnvironmentType =
  | 'lab'
  | 'staging'
  | 'production'
  | 'custom';


export type EnvironmentStatus =
  | 'draft'
  | 'ready'
  | 'degraded'
  | 'offline'
  | 'archived';


export type ServiceRole =
  | 'kubernetes'
  | 'argocd'
  | 'container_registry'
  | 'gitops_repository'
  | 'storage'
  | 'ai_provider'
  | 'custom_http_service';


export type InfrastructureProviderType =
  | 'gitlab'
  | 'nexus'
  | 'argocd'
  | 'kubernetes'
  | 'nfs'
  | 'ollama'
  | 'litellm'
  | 'vllm'
  | 'openai_compatible'
  | 'generic_http';


export type IntegrationStatus =
  | 'not_configured'
  | 'unchecked'
  | 'online'
  | 'degraded'
  | 'offline';


export interface AvailableConnection {
  id: number;
  name: string;

  providerType:
    InfrastructureProviderType;

  baseUrl: string;
  description: string | null;

  status:
    IntegrationStatus;

  lastCheckedAt: string | null;
  lastLatencyMs: number | null;
}


export interface EnvironmentService {
  role: ServiceRole;
  required: boolean;

  connectionId: number;
  connectionName: string;

  providerType:
    InfrastructureProviderType;

  baseUrl: string;

  status:
    IntegrationStatus;

  lastCheckedAt: string | null;
  lastLatencyMs: number | null;
}


export interface DeploymentEnvironment {
  id: number;

  name: string;
  code: string;

  environmentType:
    EnvironmentType;

  description: string | null;

  namespace: string;
  domain: string | null;

  configurationStatus: string;

  effectiveStatus:
    EnvironmentStatus;

  isDefault: boolean;

  serviceTotal: number;
  serviceOnline: number;

  projectCount: number;

  kubernetesConnectionName:
    string | null;

  lastCheckedAt: string | null;

  services:
    EnvironmentService[];

  createdAt: string;
  updatedAt: string;
}


export interface InfrastructureSummary {
  total: number;
  ready: number;
  degraded: number;
  offline: number;
  draft: number;
}


export interface InfrastructureOverview {
  connections:
    AvailableConnection[];

  environments:
    DeploymentEnvironment[];

  summary:
    InfrastructureSummary;
}


export interface SaveEnvironmentRequest {
  name: string;

  environmentType:
    EnvironmentType;

  description: string | null;

  namespace: string;
  domain: string | null;

  connectionIds: Partial<
    Record<ServiceRole, number>
  >;
}


export interface ArchivedEnvironment {
  id: number;
  name: string;
}


interface OverviewResponse {
  success: boolean;
  data: InfrastructureOverview;
}


interface EnvironmentResponse {
  success: boolean;

  data: {
    environment:
      DeploymentEnvironment;
  };
}


interface ArchiveEnvironmentResponse {
  success: boolean;

  data: {
    archivedEnvironment:
      ArchivedEnvironment;
  };
}


@Injectable({
  providedIn: 'root',
})
export class InfrastructureService {
  private readonly http =
    inject(HttpClient);

  private readonly auth =
    inject(Auth);


  getOverview(
    environmentType:
      EnvironmentType | null,
  ): Observable<InfrastructureOverview> {
    let params =
      new HttpParams();


    if (environmentType !== null) {
      params = params.set(
        'environmentType',
        environmentType,
      );
    }


    return this.http
      .get<OverviewResponse>(
        '/api/infrastructure/overview',

        {
          headers:
            this.createHeaders(),

          params,
        },
      )
      .pipe(
        map(
          (
            response:
              OverviewResponse,
          ) =>
            response.data,
        ),
      );
  }


  createEnvironment(
    request:
      SaveEnvironmentRequest,
  ): Observable<DeploymentEnvironment> {
    return this.http
      .post<EnvironmentResponse>(
        '/api/infrastructure/environments',

        request,

        {
          headers:
            this.createHeaders(),
        },
      )
      .pipe(
        map(
          (
            response:
              EnvironmentResponse,
          ) =>
            response.data.environment,
        ),
      );
  }


  updateEnvironment(
    environmentId: number,

    request:
      SaveEnvironmentRequest,
  ): Observable<DeploymentEnvironment> {
    return this.http
      .put<EnvironmentResponse>(
        (
          '/api/infrastructure/'
          + 'environments/'
          + environmentId
        ),

        request,

        {
          headers:
            this.createHeaders(),
        },
      )
      .pipe(
        map(
          (
            response:
              EnvironmentResponse,
          ) =>
            response.data.environment,
        ),
      );
  }


  archiveEnvironment(
    environmentId: number,
  ): Observable<ArchivedEnvironment> {
    return this.http
      .delete<ArchiveEnvironmentResponse>(
        (
          '/api/infrastructure/'
          + 'environments/'
          + environmentId
        ),

        {
          headers:
            this.createHeaders(),
        },
      )
      .pipe(
        map(
          (
            response:
              ArchiveEnvironmentResponse,
          ) =>
            response.data
              .archivedEnvironment,
        ),
      );
  }


  private createHeaders():
    HttpHeaders {
    const token =
      this.auth.getAccessToken();


    if (!token) {
      return new HttpHeaders();
    }


    return new HttpHeaders({
      Authorization:
        `Bearer ${token}`,
    });
  }
}