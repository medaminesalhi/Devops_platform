import {
  Injectable,
  inject,
} from '@angular/core';

import {
  HttpClient,
  HttpHeaders,
} from '@angular/common/http';

import {
  Observable,
  map,
} from 'rxjs';

import {
  Auth,
} from '../auth/auth';


export type ProviderType =
  | 'gitlab'
  | 'nexus'
  | 'argocd'
  | 'kubernetes'
  | 'ollama'
  | 'generic_http';


export type AuthenticationType =
  | 'none'
  | 'token'
  | 'basic';


export type ConnectionStatus =
  | 'not_configured'
  | 'unchecked'
  | 'online'
  | 'degraded'
  | 'offline';


export interface IntegrationConnection {
  id: number;

  name: string;
  providerType: ProviderType;

  baseUrl: string;
  environment: string;
  description: string | null;

  enabled: boolean;

  monitoringEnabled: boolean;
  checkIntervalSeconds: number;
  failureThreshold: number;

  status: ConnectionStatus;
  consecutiveFailures: number;

  lastHttpStatus: number | null;
  lastError: string | null;
  lastCheckedAt: string | null;
  lastLatencyMs: number | null;

  authType: AuthenticationType;
  username: string | null;
  credentialConfigured: boolean;

  createdAt: string;
  updatedAt: string;
}


export interface IntegrationConfiguration {
  connectionId?: number;

  name: string;
  providerType: ProviderType;

  baseUrl: string;
  environment: string;
  description: string | null;

  authType: AuthenticationType;
  username: string | null;
  credential: string | null;

  monitoringEnabled: boolean;
  checkIntervalSeconds: number;
  failureThreshold: number;
}


export interface IntegrationTestResult {
  status: ConnectionStatus;

  http_status: number | null;
  latency_ms: number;

  message: string;
  checked_url: string | null;

  server_reachable: boolean;
  authenticated: boolean | null;
}


interface ConnectionsResponse {
  success: boolean;

  data: {
    connections: IntegrationConnection[];
  };
}


interface ConnectionResponse {
  success: boolean;

  data: {
    connection: IntegrationConnection;
  };
}


interface DraftTestResponse {
  success: boolean;

  data: {
    test: IntegrationTestResult;
  };
}


interface SavedTestResponse {
  success: boolean;

  data: {
    connection: IntegrationConnection;
    test: IntegrationTestResult;
  };
}


@Injectable({
  providedIn: 'root',
})
export class IntegrationsService {
  private readonly http =
    inject(HttpClient);

  private readonly auth =
    inject(Auth);


  getAll():
    Observable<IntegrationConnection[]> {
    return this.http
      .get<ConnectionsResponse>(
        '/api/integrations',
        {
          headers: this.createHeaders(),
        },
      )
      .pipe(
        map(
          (response) =>
            response.data.connections,
        ),
      );
  }


  create(
    configuration:
      IntegrationConfiguration,
  ): Observable<IntegrationConnection> {
    return this.http
      .post<ConnectionResponse>(
        '/api/integrations',
        configuration,
        {
          headers: this.createHeaders(),
        },
      )
      .pipe(
        map(
          (response) =>
            response.data.connection,
        ),
      );
  }


  update(
    connectionId: number,
    configuration:
      IntegrationConfiguration,
  ): Observable<IntegrationConnection> {
    return this.http
      .put<ConnectionResponse>(
        `/api/integrations/${connectionId}`,
        configuration,
        {
          headers: this.createHeaders(),
        },
      )
      .pipe(
        map(
          (response) =>
            response.data.connection,
        ),
      );
  }


  testDraft(
    configuration:
      IntegrationConfiguration,
  ): Observable<IntegrationTestResult> {
    return this.http
      .post<DraftTestResponse>(
        '/api/integrations/test',
        configuration,
        {
          headers: this.createHeaders(),
        },
      )
      .pipe(
        map(
          (response) =>
            response.data.test,
        ),
      );
  }


  testSaved(
    connectionId: number,
  ): Observable<SavedTestResponse['data']> {
    return this.http
      .post<SavedTestResponse>(
        (
          `/api/integrations/` +
          `${connectionId}/test`
        ),
        {},
        {
          headers: this.createHeaders(),
        },
      )
      .pipe(
        map(
          (response) => response.data,
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