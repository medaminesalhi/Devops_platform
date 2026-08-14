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


export interface DashboardMetrics {
  totalProjects: number;
  activeProjects: number;
  deploymentsToday: number;
  runningDeployments: number;
  successfulDeployments7d: number;
  failedDeployments7d: number;
  successRate7d: number;
}


export interface ProjectStatusSummary {
  status: string;
  count: number;
}


export interface RecentDeployment {
  id: number;
  projectId: number;
  projectName: string;
  projectSlug: string;
  environment: string;
  status: string;
  commitSha: string | null;
  imageTag: string | null;
  triggeredBy: string;
  createdAt: string;
  startedAt: string | null;
  finishedAt: string | null;
}


export interface DashboardServices {
  api: string;
  database: string;
  gitlab: string;
  nexus: string;
  argoCd: string;
  kubernetes: string;
  ollama: string;
}


export interface DashboardOverview {
  metrics: DashboardMetrics;
  projectStatus: ProjectStatusSummary[];
  recentDeployments: RecentDeployment[];
  services: DashboardServices;
  generatedAt: string;
}


interface DashboardResponse {
  success: boolean;
  data: DashboardOverview;
}


@Injectable({
  providedIn: 'root',
})
export class DashboardService {
  private readonly http =
    inject(HttpClient);

  private readonly auth =
    inject(Auth);


  getOverview():
    Observable<DashboardOverview> {
    const token =
      this.auth.getAccessToken();

    const headers =
      token
        ? new HttpHeaders({
            Authorization:
              `Bearer ${token}`,
          })
        : new HttpHeaders();

    return this.http
      .get<DashboardResponse>(
        '/api/dashboard/overview',
        {
          headers,
        },
      )
      .pipe(
        map(
          (response) =>
            response.data,
        ),
      );
  }
}