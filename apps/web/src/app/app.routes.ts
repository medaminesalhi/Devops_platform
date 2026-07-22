import {
  Routes,
} from '@angular/router';

import {
  authGuard,
} from './core/auth/auth.guard';

import {
  Login,
} from './features/auth/pages/login/login';

import {
  Dashboard,
} from './features/dashboard/pages/dashboard/dashboard';

import {
  Infrastructure,
} from './features/infrastructure/pages/infrastructure/infrastructure';

import {
  Integrations,
} from './features/integrations/pages/integrations/integrations';

import {
  NewProject,
} from './features/projects/pages/new-project/new-project';

import {
  ProjectDetail,
} from './features/projects/pages/project-detail/project-detail';

import {
  Projects,
} from './features/projects/pages/projects/projects';

import {
  MainLayout,
} from './layout/main-layout/main-layout';


export const routes: Routes = [
  {
    path: 'login',
    component: Login,
  },

  {
    path: '',
    component: MainLayout,

    canActivate: [
      authGuard,
    ],

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
        path: 'projects/:projectId',
        component: ProjectDetail,
      },

      {
        path: 'projects',
        component: Projects,
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