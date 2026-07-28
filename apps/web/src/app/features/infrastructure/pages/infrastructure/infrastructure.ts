import {
  Component,
  OnInit,
  computed,
  inject,
  signal,
} from '@angular/core';

import {
  DatePipe,
} from '@angular/common';

import {
  FormBuilder,
  ReactiveFormsModule,
  Validators,
} from '@angular/forms';

import {
  HttpErrorResponse,
} from '@angular/common/http';

import {
  finalize,
} from 'rxjs';

import {
  AvailableConnection,
  CreateEnvironmentRequest,
  DeploymentEnvironment,
  EnvironmentStatus,
  EnvironmentType,
  InfrastructureOverview,
  InfrastructureService,
  ServiceRole,
} from '../../../../core/infrastructure/infrastructure';


interface ApiErrorResponse {
  success: false;

  error: {
    code: string;
    message: string;
  };
}


@Component({
  selector: 'app-infrastructure',

  imports: [
    ReactiveFormsModule,
    DatePipe,
  ],

  templateUrl: './infrastructure.html',
  styleUrl: './infrastructure.scss',
})
export class Infrastructure implements OnInit {
  private readonly infrastructureService =
    inject(InfrastructureService);

  private readonly formBuilder =
    inject(FormBuilder);


  readonly overview =
    signal<InfrastructureOverview | null>(
      null,
    );

  readonly selectedEnvironmentId =
    signal<number | null>(null);

  readonly clientFilter =
    signal<number | null>(null);

  readonly typeFilter =
    signal<EnvironmentType | null>(null);

  readonly editorOpen =
    signal(false);

  readonly isLoading =
    signal(true);

  readonly isSaving =
    signal(false);

  readonly errorMessage =
    signal<string | null>(null);

  readonly successMessage =
    signal<string | null>(null);


  readonly environmentForm =
    this.formBuilder.nonNullable.group({
      clientId: [
        0,
        [
          Validators.required,
          Validators.min(1),
        ],
      ],

      name: [
        '',
        [
          Validators.required,
          Validators.maxLength(140),
        ],
      ],

      environmentType: [
        'lab' as EnvironmentType,
        [
          Validators.required,
        ],
      ],

      namespace: [
        '',
        [
          Validators.required,
          Validators.maxLength(120),
        ],
      ],

      domain: [
        '',
        [
          Validators.maxLength(255),
        ],
      ],

      description: [
        '',
        [
          Validators.maxLength(1000),
        ],
      ],

      kubernetesConnectionId: [
        0,
      ],

      argocdConnectionId: [
        0,
      ],

      registryConnectionId: [
        0,
      ],

      gitopsConnectionId: [
        0,
      ],

      aiConnectionId: [
        0,
      ],
    });


  readonly selectedEnvironment = computed(
    (): DeploymentEnvironment | null => {
      const currentOverview =
        this.overview();

      const selectedId =
        this.selectedEnvironmentId();

      if (
        !currentOverview ||
        selectedId === null
      ) {
        return null;
      }

      return (
        currentOverview.environments.find(
          (
            environment:
              DeploymentEnvironment,
          ) =>
            environment.id === selectedId,
        ) ?? null
      );
    },
  );


  ngOnInit(): void {
    this.loadOverview();
  }


  loadOverview(): void {
    this.isLoading.set(true);
    this.errorMessage.set(null);

    this.infrastructureService
      .getOverview(
        this.clientFilter(),
        this.typeFilter(),
      )
      .pipe(
        finalize(() => {
          this.isLoading.set(false);
        }),
      )
      .subscribe({
        next: (
          overview: InfrastructureOverview,
        ) => {
          this.overview.set(overview);

          const selectedId =
            this.selectedEnvironmentId();

          const selectionStillExists =
            overview.environments.some(
              (
                environment:
                  DeploymentEnvironment,
              ) =>
                environment.id ===
                selectedId,
            );

          if (
            !selectionStillExists &&
            overview.environments.length > 0
          ) {
            this.selectedEnvironmentId.set(
              overview.environments[0].id,
            );
          }

          if (
            overview.environments.length === 0
          ) {
            this.selectedEnvironmentId.set(
              null,
            );
          }
        },

        error: (
          error: HttpErrorResponse,
        ) => {
          this.errorMessage.set(
            this.resolveError(error),
          );
        },
      });
  }


  setClientFilter(
    rawValue: string,
  ): void {
    this.clientFilter.set(
      rawValue
        ? Number(rawValue)
        : null,
    );

    this.loadOverview();
  }


  setTypeFilter(
    rawValue: string,
  ): void {
    this.typeFilter.set(
      rawValue
        ? rawValue as EnvironmentType
        : null,
    );

    this.loadOverview();
  }


  selectEnvironment(
    environment: DeploymentEnvironment,
  ): void {
    this.selectedEnvironmentId.set(
      environment.id,
    );
  }


  openCreateEditor(): void {
    const currentOverview =
      this.overview();

    const firstClientId =
      currentOverview?.clients[0]?.id ?? 0;

    this.environmentForm.reset({
      clientId: firstClientId,
      name: '',
      environmentType: 'lab',
      namespace: '',
      domain: '',
      description: '',
      kubernetesConnectionId: 0,
      argocdConnectionId: 0,
      registryConnectionId: 0,
      gitopsConnectionId: 0,
      aiConnectionId: 0,
    });

    this.errorMessage.set(null);
    this.successMessage.set(null);

    this.editorOpen.set(true);
  }


  closeEditor(): void {
    if (this.isSaving()) {
      return;
    }

    this.editorOpen.set(false);
  }


  createEnvironment(): void {
    this.environmentForm.markAllAsTouched();

    if (
      this.environmentForm.invalid ||
      this.isSaving()
    ) {
      return;
    }

    const values =
      this.environmentForm.getRawValue();

    const connectionIds:
      Partial<Record<ServiceRole, number>> =
        {};

    this.addConnectionIfSelected(
      connectionIds,
      'kubernetes',
      values.kubernetesConnectionId,
    );

    this.addConnectionIfSelected(
      connectionIds,
      'argocd',
      values.argocdConnectionId,
    );

    this.addConnectionIfSelected(
      connectionIds,
      'container_registry',
      values.registryConnectionId,
    );

    this.addConnectionIfSelected(
      connectionIds,
      'gitops_repository',
      values.gitopsConnectionId,
    );

    this.addConnectionIfSelected(
      connectionIds,
      'ai_provider',
      values.aiConnectionId,
    );

    const request:
      CreateEnvironmentRequest = {
        clientId:
          values.clientId,

        name:
          values.name.trim(),

        environmentType:
          values.environmentType,

        namespace:
          values.namespace.trim(),

        domain:
          values.domain.trim() || null,

        description:
          values.description.trim() || null,

        connectionIds,
      };

    this.isSaving.set(true);
    this.errorMessage.set(null);
    this.successMessage.set(null);

    this.infrastructureService
      .createEnvironment(request)
      .pipe(
        finalize(() => {
          this.isSaving.set(false);
        }),
      )
      .subscribe({
        next: (
          environment:
            DeploymentEnvironment,
        ) => {
          this.editorOpen.set(false);

          this.successMessage.set(
            'Environnement créé avec succès.',
          );

          this.loadOverview();

          this.selectedEnvironmentId.set(
            environment.id,
          );
        },

        error: (
          error: HttpErrorResponse,
        ) => {
          this.errorMessage.set(
            this.resolveError(error),
          );
        },
      });
  }


  connectionsForRole(
    serviceRole: ServiceRole,
  ): AvailableConnection[] {
    const currentOverview =
      this.overview();

    if (!currentOverview) {
      return [];
    }

    const providerByRole:
      Record<ServiceRole, string> = {
        kubernetes: 'kubernetes',
        argocd: 'argocd',
        container_registry: 'nexus',
        gitops_repository: 'gitlab',
        ai_provider: 'ollama',
      };

    const selectedClientId =
      this.environmentForm.controls
        .clientId.value;

    return currentOverview.connections.filter(
      (
        connection:
          AvailableConnection,
      ) =>
        connection.providerType ===
          providerByRole[serviceRole]

        && (
          connection.scope === 'global'

          || connection.clientId ===
            selectedClientId
        ),
    );
  }


  environmentTypeLabel(
    type: EnvironmentType,
  ): string {
    const labels:
      Record<EnvironmentType, string> = {
        lab: 'Lab',
        staging: 'Staging',
        production: 'Production',
        custom: 'Personnalisé',
      };

    return labels[type];
  }


  environmentStatusLabel(
    status: EnvironmentStatus,
  ): string {
    const labels:
      Record<EnvironmentStatus, string> = {
        draft: 'Brouillon',
        ready: 'Opérationnel',
        degraded: 'Dégradé',
        offline: 'Hors ligne',
        archived: 'Archivé',
      };

    return labels[status];
  }


  serviceRoleLabel(
    role: ServiceRole,
  ): string {
    const labels:
      Record<ServiceRole, string> = {
        kubernetes: 'Kubernetes',
        argocd: 'Argo CD',
        container_registry:
          'Registry Nexus',
        gitops_repository:
          'Repository GitOps',
        ai_provider: 'Fournisseur IA',
      };

    return labels[role];
  }


  private addConnectionIfSelected(
    connections:
      Partial<Record<ServiceRole, number>>,
    role: ServiceRole,
    connectionId: number,
  ): void {
    if (connectionId > 0) {
      connections[role] = connectionId;
    }
  }


  private resolveError(
    error: HttpErrorResponse,
  ): string {
    if (error.status === 0) {
      return (
        'Le backend Flask est inaccessible. ' +
        'Vérifiez le port 5000 et le proxy Angular.'
      );
    }

    const response =
      error.error as ApiErrorResponse | null;

    if (response?.error?.message) {
      return response.error.message;
    }

    return (
      `Erreur HTTP ` +
      `${error.status || 'inconnue'}.`
    );
  }
}