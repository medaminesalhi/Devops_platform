import {
  inject,
  Injectable,
} from '@angular/core';

import {
  HttpClient,
  HttpHeaders,
  HttpParams,
} from '@angular/common/http';

import {
  map,
  Observable,
} from 'rxjs';

import {
  AccountStatus,
  Auth,
} from '../auth/auth';


export interface AdminSummary {
  total_users: number;
  pending_users: number;
  active_users: number;
  rejected_users: number;
  suspended_users: number;
  total_deployments: number;
  deployments_today: number;
  succeeded_deployments: number;
  failed_deployments: number;
  active_deployments: number;
}


export interface AdminRole {
  code: string;
  name: string;
  description: string | null;
}


export interface AdminUser {
  id: number;
  username: string;
  email: string;
  firstName: string | null;
  lastName: string | null;
  company: string | null;
  status: AccountStatus;
  isActive: boolean;
  roles: string[];
  lastLoginAt: string | null;
  createdAt: string;
  updatedAt: string;
  approvedAt: string | null;
  approvedBy: number | null;
  rejectedAt: string | null;
  rejectionReason: string | null;
  suspendedAt: string | null;
  deploymentCount: number;
  lastDeploymentAt: string | null;
  successfulLoginCount: number;
}


export interface AdminLoginHistory {
  id: number;
  success: boolean;
  failureReason: string | null;
  ipAddress: string | null;
  userAgent: string | null;
  loggedAt: string;
}


export interface AdminDeployment {
  id: number;
  projectId: number;
  projectName: string;
  environmentId: number | null;
  environmentName: string | null;
  environmentCode: string | null;
  version: string | null;
  status: string;
  progress: number;
  currentStage: string | null;
  currentStageLabel: string | null;
  createdAt: string;
  startedAt: string | null;
  finishedAt: string | null;
  errorCode: string | null;
  errorMessage: string | null;
}


export interface AdminUserDetail {
  user: AdminUser;
  logins: AdminLoginHistory[];
  deployments: AdminDeployment[];
}


interface OverviewResponse {
  success: boolean;
  data: {
    summary: AdminSummary;
    roles: AdminRole[];
  };
}


interface UsersResponse {
  success: boolean;
  data: {
    users: AdminUser[];
  };
}


interface UserDetailResponse {
  success: boolean;
  data: AdminUserDetail;
}


interface MessageResponse {
  success: boolean;
  data: {
    message: string;
  };
}


@Injectable({
  providedIn: 'root',
})
export class AdminApi {
  private readonly http = inject(HttpClient);
  private readonly auth = inject(Auth);


  getOverview(): Observable<{
    summary: AdminSummary;
    roles: AdminRole[];
  }> {
    return this.http
      .get<OverviewResponse>(
        '/api/admin/overview',
        {
          headers: this.headers(),
        },
      )
      .pipe(
        map((response) => response.data),
      );
  }


  listUsers(
    filters: {
      search?: string;
      status?: string;
      role?: string;
    } = {},
  ): Observable<AdminUser[]> {
    let params = new HttpParams();

    if (filters.search) {
      params = params.set('search', filters.search);
    }

    if (filters.status) {
      params = params.set('status', filters.status);
    }

    if (filters.role) {
      params = params.set('role', filters.role);
    }

    return this.http
      .get<UsersResponse>(
        '/api/admin/users',
        {
          headers: this.headers(),
          params,
        },
      )
      .pipe(
        map((response) => response.data.users),
      );
  }


  getUser(
    userId: number,
  ): Observable<AdminUserDetail> {
    return this.http
      .get<UserDetailResponse>(
        `/api/admin/users/${userId}`,
        {
          headers: this.headers(),
        },
      )
      .pipe(
        map((response) => response.data),
      );
  }


  approveUser(
    userId: number,
    roleCode: string,
  ): Observable<string> {
    return this.postAction(
      `/api/admin/users/${userId}/approve`,
      {
        roleCode,
      },
    );
  }


  rejectUser(
    userId: number,
    reason = '',
  ): Observable<string> {
    return this.postAction(
      `/api/admin/users/${userId}/reject`,
      {
        reason,
      },
    );
  }


  suspendUser(
    userId: number,
  ): Observable<string> {
    return this.postAction(
      `/api/admin/users/${userId}/suspend`,
      {},
    );
  }


  activateUser(
    userId: number,
  ): Observable<string> {
    return this.postAction(
      `/api/admin/users/${userId}/activate`,
      {},
    );
  }


  updateRole(
    userId: number,
    roleCode: string,
  ): Observable<string> {
    return this.http
      .put<MessageResponse>(
        `/api/admin/users/${userId}/role`,
        {
          roleCode,
        },
        {
          headers: this.headers(),
        },
      )
      .pipe(
        map((response) => response.data.message),
      );
  }


  private postAction(
    url: string,
    body: object,
  ): Observable<string> {
    return this.http
      .post<MessageResponse>(
        url,
        body,
        {
          headers: this.headers(),
        },
      )
      .pipe(
        map((response) => response.data.message),
      );
  }


  private headers(): HttpHeaders {
    const token = this.auth.getAccessToken();

    return token
      ? new HttpHeaders({
          Authorization: `Bearer ${token}`,
        })
      : new HttpHeaders();
  }
}
