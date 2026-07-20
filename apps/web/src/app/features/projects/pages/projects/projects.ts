import { Component } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';

type ProjectStatus =
  | 'up-to-date'
  | 'update-available'
  | 'deploying'
  | 'attention';

interface Project {
  id: number;
  name: string;
  description: string;
  technology: string;
  repository: string;
  branch: string;
  environment: string;
  deployedCommit: string;
  latestCommit: string;
  status: ProjectStatus;
  updatedAt: string;
}

@Component({
  selector: 'app-projects',
  imports: [
    FormsModule,
    RouterLink,
  ],
  templateUrl: './projects.html',
  styleUrl: './projects.scss',
})
export class Projects {
  searchTerm = '';
  statusFilter = 'all';
  technologyFilter = 'all';
  informationMessage: string | null = null;

  readonly projects: Project[] = [
    {
      id: 1,
      name: 'boutique-web',
      description: 'Interface e-commerce Angular',
      technology: 'Angular',
      repository: 'client/boutique-web',
      branch: 'main',
      environment: 'Lab',
      deployedCommit: 'a81bc24',
      latestCommit: 'a81bc24',
      status: 'up-to-date',
      updatedAt: 'Il y a 5 minutes',
    },
    {
      id: 2,
      name: 'payment-api',
      description: 'API de gestion des paiements',
      technology: 'Flask',
      repository: 'services/payment-api',
      branch: 'main',
      environment: 'Lab',
      deployedCommit: 'c81ad10',
      latestCommit: 'b92de31',
      status: 'update-available',
      updatedAt: 'Il y a 12 minutes',
    },
    {
      id: 3,
      name: 'customer-service',
      description: 'Gestion des comptes clients',
      technology: 'Spring Boot',
      repository: 'services/customer-service',
      branch: 'develop',
      environment: 'Lab',
      deployedCommit: 'c19fa82',
      latestCommit: 'c19fa82',
      status: 'attention',
      updatedAt: 'Il y a 1 heure',
    },
    {
      id: 4,
      name: 'analytics-dashboard',
      description: 'Dashboard de supervision',
      technology: 'Angular',
      repository: 'analytics/dashboard',
      branch: 'main',
      environment: 'Lab',
      deployedCommit: 'e71ab09',
      latestCommit: 'f82de11',
      status: 'deploying',
      updatedAt: 'Maintenant',
    },
  ];

  get filteredProjects(): Project[] {
    const normalizedSearch = this.searchTerm
      .trim()
      .toLowerCase();

    return this.projects.filter((project) => {
      const matchesSearch =
        normalizedSearch.length === 0 ||
        project.name.toLowerCase().includes(normalizedSearch) ||
        project.repository
          .toLowerCase()
          .includes(normalizedSearch) ||
        project.technology
          .toLowerCase()
          .includes(normalizedSearch);

      const matchesStatus =
        this.statusFilter === 'all' ||
        project.status === this.statusFilter;

      const matchesTechnology =
        this.technologyFilter === 'all' ||
        project.technology === this.technologyFilter;

      return (
        matchesSearch &&
        matchesStatus &&
        matchesTechnology
      );
    });
  }

  get totalProjects(): number {
    return this.projects.length;
  }

  get upToDateProjects(): number {
    return this.projects.filter(
      (project) => project.status === 'up-to-date',
    ).length;
  }

  get availableUpdates(): number {
    return this.projects.filter(
      (project) =>
        project.status === 'update-available',
    ).length;
  }

  get projectsRequiringAttention(): number {
    return this.projects.filter(
      (project) => project.status === 'attention',
    ).length;
  }

  statusLabel(status: ProjectStatus): string {
    const labels: Record<ProjectStatus, string> = {
      'up-to-date': 'À jour',
      'update-available': 'Mise à jour disponible',
      deploying: 'Déploiement en cours',
      attention: 'Intervention requise',
    };

    return labels[status];
  }

  selectAction(message: string): void {
    this.informationMessage = message;
  }

  clearFilters(): void {
    this.searchTerm = '';
    this.statusFilter = 'all';
    this.technologyFilter = 'all';
  }

  closeMessage(): void {
    this.informationMessage = null;
  }
}