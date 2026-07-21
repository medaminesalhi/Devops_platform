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

  /*
   * Cette propriété reste présente dans la réponse
   * du backend pour compatibilité, mais elle n'est
   * plus affichée dans la page Intégrations.
   */
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


export interface SavedConnectionResult {
  connection: IntegrationConnection;
  test: IntegrationTestResult | null;
  testError: string | null;
}


export interface DeletedConnectionResult {
  id: number;
  name: string;
}


interface ConnectionsResponse {
  success: boolean;

  data: {
    connections: IntegrationConnection[];
  };
}


interface ConnectionMutationResponse {
  success: boolean;
  data: SavedConnectionResult;
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


interface DeleteConnectionResponse {
  success: boolean;

  data: {
    deletedConnection: DeletedConnectionResult;
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
          (
            response: ConnectionsResponse,
          ) => response.data.connections,
        ),
      );
  }


  create(
    configuration:
      IntegrationConfiguration,
  ): Observable<SavedConnectionResult> {
    return this.http
      .post<ConnectionMutationResponse>(
        '/api/integrations',
        configuration,
        {
          headers: this.createHeaders(),
        },
      )
      .pipe(
        map(
          (
            response:
              ConnectionMutationResponse,
          ) => response.data,
        ),
      );
  }


  update(
    connectionId: number,
    configuration:
      IntegrationConfiguration,
  ): Observable<SavedConnectionResult> {
    return this.http
      .put<ConnectionMutationResponse>(
        `/api/integrations/${connectionId}`,
        configuration,
        {
          headers: this.createHeaders(),
        },
      )
      .pipe(
        map(
          (
            response:
              ConnectionMutationResponse,
          ) => response.data,
        ),
      );
  }


  delete(
    connectionId: number,
  ): Observable<DeletedConnectionResult> {
    return this.http
      .delete<DeleteConnectionResponse>(
        `/api/integrations/${connectionId}`,
        {
          headers: this.createHeaders(),
        },
      )
      .pipe(
        map(
          (
            response:
              DeleteConnectionResponse,
          ) =>
            response.data.deletedConnection,
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
          (
            response: DraftTestResponse,
          ) => response.data.test,
        ),
      );
  }


  testSaved(
    connectionId: number,
  ): Observable<SavedTestResponse['data']> {
    return this.http
      .post<SavedTestResponse>(
        `/api/integrations/${connectionId}/test`,
        {},
        {
          headers: this.createHeaders(),
        },
      )
      .pipe(
        map(
          (
            response: SavedTestResponse,
          ) => response.data,
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