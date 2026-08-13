import {
  inject,
} from '@angular/core';

import {
  CanActivateFn,
  Router,
} from '@angular/router';

import {
  Auth,
} from './auth';


export const adminGuard: CanActivateFn = () => {
  const auth = inject(Auth);
  const router = inject(Router);

  if (auth.isAuthenticated() && auth.hasRole('admin')) {
    return true;
  }

  return router.createUrlTree([
    '/dashboard',
  ]);
};
