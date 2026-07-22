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


export type RepositoryVisibility =
  | 'public'
  | 'private';


export type GitTransport =
  | 'https'
  | 'ssh';


export type CredentialSource =
  | 'none'
  | 'integration'
  | 'project';


export type SourceAuthMethod =
  | 'none'
  | 'https_password'
  | 'https_token'
  | 'ssh_key';


export type GitTokenType =
  | 'personal_access_token'
  | 'project_access_token'
  | 'group_access_token'
  | 'deploy_token'
  | 'generic_token';


export type ProjectStatus =
  | 'draft'
  | 'active'
  | 'source_error'
  | 'archived';


export interface GitConnectionOption {
  id: number;

  name: string;
  baseUrl: string;

  status: string;
  verifySsl: boolean;

  sshHost: string | null;
  sshPort: number;
  sshUsername: string;

  credentialConfigured: boolean;

  credentialAuthType:
    | 'none'
    | 'basic'
    | 'token'
    | 'ssh_key';

  credentialUsername: string | null;
}


export interface ProjectEnvironmentOption {
  id: number;
  name: string;

  environmentType: string;

  namespace: string;
  domain: string | null;

  configurationStatus: string;
}


export interface ProjectOptions {
  gitConnections: GitConnectionOption[];

  environments:
    ProjectEnvironmentOption[];
}


export interface SourceValidationResult {
  repositoryUrl: string;
  repositoryPath: string;
  repositoryHost: string;

  branch: string;
  commitSha: string;

  visibility: RepositoryVisibility;
  transport: GitTransport;

  validationMethod: string;
}


export interface ValidateSourceRequest {
  sourceConnectionId: number;

  repositoryUrl: string;

  visibility: RepositoryVisibility;
  transport: GitTransport;

  credentialSource: CredentialSource;
  authMethod: SourceAuthMethod;

  tokenType: GitTokenType | null;

  username: string | null;
  secret: string | null;

  branch: string;

  sourceSubdirectory: string | null;
}


export interface CreateProjectRequest
  extends ValidateSourceRequest {
  name: string;

  description: string | null;

  allowedEnvironmentIds: number[];

  defaultEnvironmentId: number;
}


export interface ProjectSource {
  connectionId: number | null;
  connectionName: string | null;

  baseUrl: string | null;

  repositoryUrl: string | null;
  repositoryPath: string | null;

  visibility: RepositoryVisibility;
  transport: GitTransport;

  credentialSource: CredentialSource;
  authMethod: SourceAuthMethod;

  tokenType: GitTokenType | null;

  username: string | null;

  credentialConfigured: boolean;

  branch: string;
  subdirectory: string | null;

  status: string;
  error: string | null;

  lastCommitSha: string | null;
  lastCheckedAt: string | null;
}


export interface ProjectEnvironment {
  id: number;
  name: string;

  environmentType: string;
  namespace: string;

  isDefault: boolean;
}


export interface ProjectDefaultEnvironment {
  id: number;
  name: string;

  environmentType: string;
  namespace: string;
}


export interface Project {
  id: number;

  name: string;
  slug: string;

  description: string | null;

  status: ProjectStatus;

  source: ProjectSource;

  defaultEnvironment:
    ProjectDefaultEnvironment | null;

  environments:
    ProjectEnvironment[];

  createdBy: number | null;

  createdAt: string | null;
  updatedAt: string | null;
}


export interface ProjectCreationResult {
  project: Project;

  sourceValidation:
    SourceValidationResult;
}


export interface ProjectFilters {
  status?: ProjectStatus | null;

  search?: string | null;
}


export interface ProjectListResult {
  projects: Project[];

  total: number;
}


interface OptionsResponse {
  success: boolean;

  data: ProjectOptions;
}


interface ValidationResponse {
  success: boolean;

  data: {
    sourceValidation:
      SourceValidationResult;
  };
}


interface CreationResponse {
  success: boolean;

  data: ProjectCreationResult;
}


interface ProjectListResponse {
  success: boolean;

  data: ProjectListResult;
}


interface ProjectDetailResponse {
  success: boolean;

  data: {
    project: Project;
  };
}


@Injectable({
  providedIn: 'root',
})
export class ProjectsService {
  private readonly http =
    inject(HttpClient);

  private readonly auth =
    inject(Auth);


  getOptions():
    Observable<ProjectOptions> {
    return this.http
      .get<OptionsResponse>(
        '/api/projects/options',
        {
          headers: this.createHeaders(),
        },
      )
      .pipe(
        map(
          response =>
            response.data,
        ),
      );
  }


  validateSource(
    request: ValidateSourceRequest,
  ): Observable<SourceValidationResult> {
    return this.http
      .post<ValidationResponse>(
        '/api/projects/validate-source',
        request,
        {
          headers: this.createHeaders(),
        },
      )
      .pipe(
        map(
          response =>
            response.data.sourceValidation,
        ),
      );
  }


  createProject(
    request: CreateProjectRequest,
  ): Observable<ProjectCreationResult> {
    return this.http
      .post<CreationResponse>(
        '/api/projects',
        request,
        {
          headers: this.createHeaders(),
        },
      )
      .pipe(
        map(
          response =>
            response.data,
        ),
      );
  }


  getProjects(
    filters: ProjectFilters = {},
  ): Observable<ProjectListResult> {
    let params =
      new HttpParams();

    const search =
      filters.search?.trim();

    if (search) {
      params = params.set(
        'search',
        search,
      );
    }

    if (filters.status) {
      params = params.set(
        'status',
        filters.status,
      );
    }

    return this.http
      .get<ProjectListResponse>(
        '/api/projects',
        {
          headers: this.createHeaders(),
          params,
        },
      )
      .pipe(
        map(
          response =>
            response.data,
        ),
      );
  }


  getProject(
    projectId: number,
  ): Observable<Project> {
    return this.http
      .get<ProjectDetailResponse>(
        `/api/projects/${projectId}`,
        {
          headers: this.createHeaders(),
        },
      )
      .pipe(
        map(
          response =>
            response.data.project,
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