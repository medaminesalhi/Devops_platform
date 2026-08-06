import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpHeaders, HttpParams } from '@angular/common/http';
import { Observable, map } from 'rxjs';
import { Auth } from '../auth/auth';

export type ProjectOperationMode = 'new_application' | 'adopt_existing';
export type ProjectSourceType = 'git' | 'zip';
export type RepositoryVisibility = 'public' | 'private';
export type GitTransport = 'https' | 'ssh';
export type SourceTransport = GitTransport | 'archive';
export type CredentialSource = 'none' | 'integration' | 'project';
export type SourceAuthMethod = 'none' | 'https_password' | 'https_token' | 'ssh_key';
export type GitTokenType =
  | 'personal_access_token'
  | 'project_access_token'
  | 'group_access_token'
  | 'deploy_token'
  | 'generic_token';
export type ProjectStatus = 'draft' | 'active' | 'source_error' | 'archived';

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
  credentialAuthType: 'none' | 'basic' | 'token' | 'ssh_key';
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

export interface ArchiveLimits {
  maxBytes: number;
  maxMegabytes: number;
  maxEntries: number;
}

export interface ProjectOptions {
  gitConnections: GitConnectionOption[];
  environments: ProjectEnvironmentOption[];
  archiveLimits: ArchiveLimits;
}

export interface ArchiveValidationDetails {
  originalName: string;
  sizeBytes: number;
  sha256: string;
  entryCount: number;
  uncompressedBytes: number;
  topLevelEntries: string[];
}

export interface SourceValidationResult {
  sourceType: ProjectSourceType;
  repositoryUrl: string | null;
  repositoryPath: string | null;
  repositoryHost: string | null;
  branch: string | null;
  commitSha: string | null;
  visibility: RepositoryVisibility | null;
  transport: SourceTransport;
  archive: ArchiveValidationDetails | null;
  validationMethod: string;
}

export interface ValidateGitSourceRequest {
  sourceType: 'git';
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

export interface CreateGitProjectRequest extends ValidateGitSourceRequest {
  operationMode: ProjectOperationMode;
  name: string;
  description: string | null;
  environmentId: number;
}

export interface CreateProjectDraftRequest {
  operationMode: ProjectOperationMode;
  name: string;
  description: string | null;
}

export interface SaveProjectEnvironmentRequest {
  environmentId: number;
}

export interface ReplaceProjectCredentialRequest {
  credentialSource: CredentialSource;
  authMethod: SourceAuthMethod;
  tokenType: GitTokenType | null;
  username: string | null;
  secret: string | null;
}

export interface ProjectArchive {
  originalName: string;
  sizeBytes: number;
  sha256: string;
  entryCount: number;
  uncompressedBytes: number;
}

export interface ProjectSource {
  type: ProjectSourceType;
  connectionId: number | null;
  connectionName: string | null;
  baseUrl: string | null;
  repositoryUrl: string | null;
  repositoryPath: string | null;
  visibility: RepositoryVisibility;
  transport: SourceTransport;
  credentialSource: CredentialSource;
  authMethod: SourceAuthMethod;
  tokenType: GitTokenType | null;
  username: string | null;
  credentialConfigured: boolean;
  branch: string;
  subdirectory: string | null;
  archive: ProjectArchive | null;
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
  operationMode: ProjectOperationMode;
  status: ProjectStatus;
  source: ProjectSource;
  defaultEnvironment: ProjectDefaultEnvironment | null;
  environments: ProjectEnvironment[];
  createdBy: number | null;
  createdAt: string | null;
  updatedAt: string | null;
}

export interface ProjectCreationResult {
  project: Project;
  sourceValidation: SourceValidationResult;
}

export interface ProjectFilters {
  status?: ProjectStatus | null;
  search?: string | null;
}

export interface ProjectListResult {
  projects: Project[];
  total: number;
}

interface ApiResponse<T> {
  success: boolean;
  data: T;
}

@Injectable({ providedIn: 'root' })
export class ProjectsService {
  private readonly http = inject(HttpClient);
  private readonly auth = inject(Auth);

  getOptions(): Observable<ProjectOptions> {
    return this.http
      .get<ApiResponse<ProjectOptions>>('/api/projects/options', { headers: this.headers() })
      .pipe(map(response => response.data));
  }

  validateSource(request: ValidateGitSourceRequest | FormData): Observable<SourceValidationResult> {
    return this.http
      .post<ApiResponse<{ sourceValidation: SourceValidationResult }>>(
        '/api/projects/validate-source',
        request,
        { headers: this.headers() },
      )
      .pipe(map(response => response.data.sourceValidation));
  }

  createProject(request: CreateGitProjectRequest | FormData): Observable<ProjectCreationResult> {
    return this.http
      .post<ApiResponse<ProjectCreationResult>>('/api/projects', request, { headers: this.headers() })
      .pipe(map(response => response.data));
  }

  createDraft(request: CreateProjectDraftRequest): Observable<Project> {
    return this.http
      .post<ApiResponse<{ project: Project }>>('/api/projects/drafts', request, { headers: this.headers() })
      .pipe(map(response => response.data.project));
  }

  saveDraftSource(
    projectId: number,
    request: ValidateGitSourceRequest | FormData,
  ): Observable<{ project: Project; sourceValidation: SourceValidationResult }> {
    return this.http
      .put<ApiResponse<{ project: Project; sourceValidation: SourceValidationResult }>>(
        `/api/projects/${projectId}/source`,
        request,
        { headers: this.headers() },
      )
      .pipe(map(response => response.data));
  }

  testStoredSource(projectId: number): Observable<SourceValidationResult> {
    return this.http
      .post<ApiResponse<{ sourceValidation: SourceValidationResult }>>(
        `/api/projects/${projectId}/source/check`,
        {},
        { headers: this.headers() },
      )
      .pipe(map(response => response.data.sourceValidation));
  }

  replaceCredential(
    projectId: number,
    request: ReplaceProjectCredentialRequest,
  ): Observable<Project> {
    return this.http
      .put<ApiResponse<{ project: Project }>>(
        `/api/projects/${projectId}/source/credential`,
        request,
        { headers: this.headers() },
      )
      .pipe(map(response => response.data.project));
  }

  saveProjectEnvironment(
    projectId: number,
    request: SaveProjectEnvironmentRequest,
  ): Observable<Project> {
    return this.http
      .put<ApiResponse<{ project: Project }>>(
        `/api/projects/${projectId}/environment`,
        request,
        { headers: this.headers() },
      )
      .pipe(map(response => response.data.project));
  }

  activateProject(projectId: number): Observable<Project> {
    return this.http
      .post<ApiResponse<{ project: Project }>>(
        `/api/projects/${projectId}/activate`,
        {},
        { headers: this.headers() },
      )
      .pipe(map(response => response.data.project));
  }

  getProjects(filters: ProjectFilters = {}): Observable<ProjectListResult> {
    let params = new HttpParams();
    const search = filters.search?.trim();

    if (search) {
      params = params.set('search', search);
    }
    if (filters.status) {
      params = params.set('status', filters.status);
    }

    return this.http
      .get<ApiResponse<ProjectListResult>>('/api/projects', {
        headers: this.headers(),
        params,
      })
      .pipe(map(response => response.data));
  }

  getProject(projectId: number): Observable<Project> {
    return this.http
      .get<ApiResponse<{ project: Project }>>(`/api/projects/${projectId}`, {
        headers: this.headers(),
      })
      .pipe(map(response => response.data.project));
  }

  private headers(): HttpHeaders {
    const token = this.auth.getAccessToken();
    return token
      ? new HttpHeaders({ Authorization: `Bearer ${token}` })
      : new HttpHeaders();
  }
}