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
  | 'ai_provider';


export interface InfrastructureClient {
  id: number;
  name: string;
  slug: string;
  status: string;
}


export interface AvailableConnection {
  id: number;
  name: string;
  providerType: string;
  baseUrl: string;
  status: string;
  scope: 'global' | 'client';
  clientId: number | null;
  clientName: string | null;
}


export interface EnvironmentService {
  role: ServiceRole;
  required: boolean;

  connectionId: number;
  connectionName: string;

  providerType: string;
  status: string;

  lastCheckedAt: string | null;
  lastLatencyMs: number | null;
}


export interface DeploymentEnvironment {
  id: number;

  clientId: number;
  clientName: string;
  clientSlug: string;

  name: string;
  code: string;

  environmentType: EnvironmentType;

  description: string | null;
  namespace: string;
  domain: string | null;

  configurationStatus: string;
  effectiveStatus: EnvironmentStatus;

  isDefault: boolean;

  serviceTotal: number;
  serviceOnline: number;

  projectCount: number;

  kubernetesConnectionName: string | null;

  lastCheckedAt: string | null;

  services: EnvironmentService[];

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
  clients: InfrastructureClient[];
  connections: AvailableConnection[];
  environments: DeploymentEnvironment[];
  summary: InfrastructureSummary;
}


export interface CreateEnvironmentRequest {
  clientId: number;

  name: string;

  environmentType: EnvironmentType;

  description: string | null;

  namespace: string;

  domain: string | null;

  connectionIds: Partial<
    Record<ServiceRole, number>
  >;
}


interface OverviewResponse {
  success: boolean;
  data: InfrastructureOverview;
}


interface EnvironmentResponse {
  success: boolean;

  data: {
    environment: DeploymentEnvironment;
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
    clientId: number | null,
    environmentType:
      EnvironmentType | null,
  ): Observable<InfrastructureOverview> {
    let params = new HttpParams();

    if (clientId !== null) {
      params = params.set(
        'clientId',
        clientId.toString(),
      );
    }

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
          headers: this.createHeaders(),
          params,
        },
      )
      .pipe(
        map(
          (
            response: OverviewResponse,
          ) => response.data,
        ),
      );
  }


  createEnvironment(
    request: CreateEnvironmentRequest,
  ): Observable<DeploymentEnvironment> {
    return this.http
      .post<EnvironmentResponse>(
        '/api/infrastructure/environments',
        request,
        {
          headers: this.createHeaders(),
        },
      )
      .pipe(
        map(
          (
            response: EnvironmentResponse,
          ) => response.data.environment,
        ),
      );
  }


  private createHeaders(): HttpHeaders {
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