import { Routes } from '@angular/router';

import { adminGuard } from './core/auth/admin.guard';
import { authGuard } from './core/auth/auth.guard';
import { Admin } from './features/admin/pages/admin/admin';
import { Login } from './features/auth/pages/login/login';
import { Register } from './features/auth/pages/register/register';
import { Dashboard } from './features/dashboard/pages/dashboard/dashboard';
import { DeploymentDetail } from './features/deployments/pages/deployment-detail/deployment-detail';
import { Deployments } from './features/deployments/pages/deployments/deployments';
import { NewDeployment } from './features/deployments/pages/new-deployment/new-deployment';
import { ProjectDeploymentGateway } from './features/deployments/pages/project-deployment-gateway/project-deployment-gateway';
import { Infrastructure } from './features/infrastructure/pages/infrastructure/infrastructure';
import { NewPerformanceTest } from './features/performance/pages/new-performance-test/new-performance-test';
import { Performance } from './features/performance/pages/performance/performance';
import { PerformanceRunDetail } from './features/performance/pages/performance-run-detail/performance-run-detail';
import { Integrations } from './features/integrations/pages/integrations/integrations';
import { NewProject } from './features/projects/pages/new-project/new-project';
import { ProjectDetail } from './features/projects/pages/project-detail/project-detail';
import { Projects } from './features/projects/pages/projects/projects';
import { Settings } from './features/settings/pages/settings/settings';
import { MainLayout } from './layout/main-layout/main-layout';

export const routes: Routes = [
  {
    path: 'login',
    component: Login,
  },
  {
    path: 'register',
    component: Register,
  },
  {
    path: '',
    component: MainLayout,
    canActivate: [authGuard],
    children: [
      {
        path: 'dashboard',
        component: Dashboard,
      },
      {
        path: 'projects/new',
        component: NewProject,
      },
      {
        path: 'projects/:projectId/configuration',
        component: ProjectDetail,
        data: { phase: 'configuration' },
      },
      {
        path: 'projects/:projectId/analysis',
        component: ProjectDetail,
        data: { phase: 'analysis' },
      },
      {
        path: 'projects/:projectId/proposal',
        component: ProjectDetail,
        data: { phase: 'proposal' },
      },
      {
        path: 'projects/:projectId/generation',
        component: ProjectDetail,
        data: { phase: 'generation' },
      },
      {
        path: 'projects/:projectId/deployment',
        component: ProjectDeploymentGateway,
      },
      {
        path: 'projects/:projectId',
        component: ProjectDetail,
        data: { phase: 'auto' },
      },
      {
        path: 'projects',
        component: Projects,
      },
      {
        path: 'deployments/new',
        component: NewDeployment,
      },
      {
        path: 'deployments/:deploymentId',
        component: DeploymentDetail,
      },
      {
        path: 'deployments',
        component: Deployments,
      },
      {
        path: 'performance/new',
        component: NewPerformanceTest,
      },
      {
        path: 'performance/runs/:runId',
        component: PerformanceRunDetail,
      },
      {
        path: 'performance',
        component: Performance,
      },
      {
        path: 'infrastructure',
        component: Infrastructure,
      },
      {
        path: 'integrations',
        component: Integrations,
      },
      {
        path: 'settings',
        component: Settings,
      },
      {
        path: 'admin',
        component: Admin,
        canActivate: [adminGuard],
      },
      {
        path: '',
        pathMatch: 'full',
        redirectTo: 'dashboard',
      },
    ],
  },
  {
    path: '**',
    redirectTo: 'dashboard',
  },
];
