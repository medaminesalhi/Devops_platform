import {
  Component,
  inject,
} from '@angular/core';

import {
  RouterLink,
  RouterLinkActive,
} from '@angular/router';

import {
  Auth,
} from '../../core/auth/auth';


@Component({
  selector: 'app-sidebar',
  imports: [
    RouterLink,
    RouterLinkActive,
  ],
  templateUrl: './sidebar.html',
  styleUrl: './sidebar.scss',
})
export class Sidebar {
  readonly auth = inject(Auth);


  get displayName(): string {
    const user = this.auth.currentUser();

    if (!user) {
      return 'Utilisateur';
    }

    const fullName = [
      user.firstName,
      user.lastName,
    ]
      .filter(Boolean)
      .join(' ')
      .trim();

    return fullName || user.username;
  }


  get initials(): string {
    const user = this.auth.currentUser();

    if (!user) {
      return 'U';
    }

    const firstLetter =
      user.firstName?.charAt(0) ??
      user.username.charAt(0);

    const secondLetter =
      user.lastName?.charAt(0) ?? '';

    return (
      firstLetter + secondLetter
    ).toUpperCase();
  }


  get roleLabel(): string {
    const roles = this.auth.currentUser()?.roles ?? [];

    if (roles.includes('admin')) {
      return 'Administrateur';
    }

    if (roles.includes('devops')) {
      return 'DevOps';
    }

    if (roles.includes('developer')) {
      return 'Développeur';
    }

    if (roles.includes('viewer')) {
      return 'Lecteur';
    }

    return 'Utilisateur';
  }


  logout(): void {
    this.auth.logout();
  }
}
