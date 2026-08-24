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
  Auth,
} from '../../../../core/auth/auth';

import {
  ArchivedEnvironment,
  AvailableConnection,
  DeploymentEnvironment,
  EnvironmentService,
  EnvironmentStatus,
  EnvironmentType,
  InfrastructureOverview,
  InfrastructureProviderType,
  InfrastructureService,
  IntegrationStatus,
  SaveEnvironmentRequest,
  ServiceRole,
} from '../../../../core/infrastructure/infrastructure';


interface ApiErrorResponse {
  success: false;

  error: {
    code: string;
    message: string;
  };
}


type ConnectionControlName =
  | 'kubernetesConnectionId'
  | 'argocdConnectionId'
  | 'registryConnectionId'
  | 'gitopsConnectionId'
  | 'storageConnectionId'
  | 'aiConnectionId'
  | 'httpServiceConnectionId';


interface ServiceDefinition {
  role: ServiceRole;

  controlName:
    ConnectionControlName;

  label: string;
  description: string;

  required: boolean;

  providers:
    InfrastructureProviderType[];
}


const SERVICE_DEFINITIONS:
  ServiceDefinition[] = [
    {
      role:
        'kubernetes',

      controlName:
        'kubernetesConnectionId',

      label:
        'Cluster Kubernetes',

      description:
        (
          'Cluster dans lequel les '
          + 'applications seront exécutées.'
        ),

      required:
        true,

      providers: [
        'kubernetes',
      ],
    },


    {
      role:
        'argocd',

      controlName:
        'argocdConnectionId',

      label:
        'Argo CD',

      description:
        (
          'Service GitOps chargé de '
          + 'synchroniser les applications.'
        ),

      required:
        true,

      providers: [
        'argocd',
      ],
    },


    {
      role:
        'container_registry',

      controlName:
        'registryConnectionId',

      label:
        'Registre de conteneurs',

      description:
        (
          'Nexus stocke les images '
          + 'construites avant '
          + 'le déploiement.'
        ),

      required:
        true,

      providers: [
        'nexus',
      ],
    },


    {
      role:
        'gitops_repository',

      controlName:
        'gitopsConnectionId',

      label:
        'Dépôt GitOps',

      description:
        (
          'Optionnel. Nécessaire uniquement si '
          + 'la source Argo CD choisie en phase 3 '
          + 'est un repository GitLab.'
        ),

      required:
        false,

      providers: [
        'gitlab',
      ],
    },


    {
      role:
        'storage',

      controlName:
        'storageConnectionId',

      label:
        'Stockage NFS',

      description:
        (
          'Stockage partagé pour '
          + 'les volumes persistants.'
        ),

      required:
        false,

      providers: [
        'nfs',
      ],
    },


    {
      role:
        'ai_provider',

      controlName:
        'aiConnectionId',

      label:
        'Fournisseur IA',

      description:
        (
          'Ollama, LiteLLM, vLLM '
          + 'ou une API compatible OpenAI.'
        ),

      required:
        false,

      providers: [
        'ollama',
        'litellm',
        'vllm',
        'openai_compatible',
      ],
    },


    {
      role:
        'custom_http_service',

      controlName:
        'httpServiceConnectionId',

      label:
        'Service HTTP personnalisé',

      description:
        (
          'Endpoint optionnel de santé '
          + 'ou de supervision.'
        ),

      required:
        false,

      providers: [
        'generic_http',
      ],
    },
  ];


@Component({
  selector:
    'app-infrastructure',

  imports: [
    ReactiveFormsModule,
    DatePipe,
  ],

  templateUrl:
    './infrastructure.html',

  styleUrl:
    './infrastructure.scss',
})
export class Infrastructure
  implements OnInit {
  private readonly infrastructureService =
    inject(
      InfrastructureService
    );

  private readonly auth =
    inject(Auth);

  readonly isAdmin =
    this.auth.isAdmin;

  private readonly formBuilder =
    inject(
      FormBuilder
    );


  readonly serviceDefinitions =
    SERVICE_DEFINITIONS;


  readonly overview =
    signal<
      InfrastructureOverview | null
    >(
      null
    );


  /*
   * Aucun environnement n'est sélectionné
   * au chargement de la page.
   */
  readonly selectedEnvironmentId =
    signal<number | null>(
      null
    );


  readonly typeFilter =
    signal<
      EnvironmentType | null
    >(
      null
    );


  readonly editorOpen =
    signal(
      false
    );


  readonly editingEnvironmentId =
    signal<number | null>(
      null
    );


  readonly isLoading =
    signal(
      true
    );


  readonly isSaving =
    signal(
      false
    );


  readonly archivingEnvironmentId =
    signal<number | null>(
      null
    );


  readonly errorMessage =
    signal<string | null>(
      null
    );


  readonly successMessage =
    signal<string | null>(
      null
    );


  readonly environmentForm =
    this.formBuilder
      .nonNullable
      .group({
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

            Validators.maxLength(
              63
            ),

            Validators.pattern(
              (
                /^[a-z0-9]+ (?:[-a-z0-9]*[a-z0-9])?$/
              )
            ),
          ],
        ],


        domain: [
          '',

          [
            Validators.maxLength(
              255
            ),
          ],
        ],


        description: [
          '',

          [
            Validators.maxLength(
              1000
            ),
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


        storageConnectionId: [
          0,
        ],


        aiConnectionId: [
          0,
        ],


        httpServiceConnectionId: [
          0,
        ],
      });


  readonly selectedEnvironment =
    computed(
      ():
        DeploymentEnvironment
        | null => {
        const currentOverview =
          this.overview();


        const selectedId =
          this.selectedEnvironmentId();


        if (
          !currentOverview

          || selectedId === null
        ) {
          return null;
        }


        return (
          currentOverview.environments
            .find(
              (
                environment:
                  DeploymentEnvironment,
              ) =>
                environment.id
                === selectedId,
            )

          ?? null
        );
      },
    );


  readonly missingRequiredServices =
    computed(
      (): string[] => {
        const environment =
          this.selectedEnvironment();


        if (!environment) {
          return [];
        }


        const configuredRoles =
          new Set(
            environment.services.map(
              (
                service:
                  EnvironmentService,
              ) =>
                service.role,
            ),
          );


        return this.serviceDefinitions
          .filter(
            (
              definition:
                ServiceDefinition,
            ) =>
              definition.required

              && !configuredRoles.has(
                definition.role,
              ),
          )
          .map(
            (
              definition:
                ServiceDefinition,
            ) =>
              definition.label,
          );
      },
    );


  readonly requiredConfiguredCount =
    computed(
      (): number => {
        const environment =
          this.selectedEnvironment();


        if (!environment) {
          return 0;
        }


        return environment.services
          .filter(
            (
              service:
                EnvironmentService,
            ) =>
              service.required,
          )
          .length;
      },
    );


  ngOnInit(): void {
    this.loadOverview();
  }


  loadOverview(): void {
    this.isLoading.set(
      true
    );

    this.errorMessage.set(
      null
    );


    this.infrastructureService
      .getOverview(
        this.typeFilter()
      )
      .pipe(
        finalize(
          () =>
            this.isLoading.set(
              false
            ),
        ),
      )
      .subscribe({
        next: (
          overview:
            InfrastructureOverview,
        ) => {
          this.overview.set(
            overview
          );


          const selectedId =
            this.selectedEnvironmentId();


          /*
           * Au premier chargement, selectedId vaut null.
           * Aucun environnement n'est sélectionné.
           */
          if (selectedId === null) {
            return;
          }


          const selectionStillExists =
            overview.environments.some(
              (
                environment:
                  DeploymentEnvironment,
              ) =>
                environment.id
                === selectedId,
            );


          if (!selectionStillExists) {
            this.selectedEnvironmentId
              .set(
                null
              );
          }
        },


        error: (
          error:
            HttpErrorResponse,
        ) => {
          this.errorMessage.set(
            this.resolveError(
              error
            ),
          );
        },
      });
  }


  setTypeFilter(
    rawValue: string,
  ): void {
    this.typeFilter.set(
      rawValue

        ? rawValue as EnvironmentType

        : null,
    );


    /*
     * Fermer le détail lors d'un changement
     * de filtre.
     */
    this.selectedEnvironmentId.set(
      null
    );


    this.loadOverview();
  }


  selectEnvironment(
    environment:
      DeploymentEnvironment,
  ): void {
    const currentId =
      this.selectedEnvironmentId();


    /*
     * Un clic ouvre le détail.
     * Un second clic sur la même ligne le ferme.
     */
    this.selectedEnvironmentId.set(
      currentId === environment.id

        ? null

        : environment.id,
    );
  }


  closeDetails(): void {
    this.selectedEnvironmentId.set(
      null
    );
  }


  openCreateEditor(): void {
    this.editingEnvironmentId.set(
      null
    );


    this.environmentForm.reset({
      name:
        '',

      environmentType:
        'lab',

      namespace:
        '',

      domain:
        '',

      description:
        '',

      kubernetesConnectionId:
        0,

      argocdConnectionId:
        0,

      registryConnectionId:
        0,

      gitopsConnectionId:
        0,

      storageConnectionId:
        0,

      aiConnectionId:
        0,

      httpServiceConnectionId:
        0,
    });


    this.clearMessages();


    this.editorOpen.set(
      true
    );
  }


  openEditEditor(
    environment:
      DeploymentEnvironment,
  ): void {
    this.editingEnvironmentId.set(
      environment.id
    );


    this.environmentForm.reset({
      name:
        environment.name,

      environmentType:
        environment.environmentType,

      namespace:
        environment.namespace,

      domain:
        environment.domain
        ?? '',

      description:
        environment.description
        ?? '',

      ...this.connectionValuesFromEnvironment(
        environment
      ),
    });


    this.clearMessages();


    this.editorOpen.set(
      true
    );
  }


  closeEditor(): void {
    if (this.isSaving()) {
      return;
    }


    this.editorOpen.set(
      false
    );
  }


  saveEnvironment(): void {
    this.environmentForm
      .markAllAsTouched();


    if (
      this.environmentForm.invalid

      || this.isSaving()
    ) {
      return;
    }


    const request =
      this.buildRequest();


    const editingId =
      this.editingEnvironmentId();


    this.isSaving.set(
      true
    );


    this.clearMessages();


    const request$ =
      editingId === null

        ? this.infrastructureService
            .createEnvironment(
              request
            )

        : this.infrastructureService
            .updateEnvironment(
              editingId,
              request,
            );


    request$
      .pipe(
        finalize(
          () =>
            this.isSaving.set(
              false
            ),
        ),
      )
      .subscribe({
        next: (
          environment:
            DeploymentEnvironment,
        ) => {
          this.editorOpen.set(
            false
          );


          this.successMessage.set(
            editingId === null

              ? (
                  'Environnement créé '
                  + 'avec succès.'
                )

              : (
                  'Environnement modifié '
                  + 'avec succès.'
                ),
          );


          /*
           * Après une création ou modification,
           * afficher le détail de l'élément concerné.
           */
          this.selectedEnvironmentId.set(
            environment.id
          );


          this.loadOverview();
        },


        error: (
          error:
            HttpErrorResponse,
        ) => {
          this.errorMessage.set(
            this.resolveError(
              error
            ),
          );
        },
      });
  }


  archiveEnvironment(
    environment:
      DeploymentEnvironment,
  ): void {
    if (
      this.archivingEnvironmentId()
      !== null
    ) {
      return;
    }


    const confirmed =
      window.confirm(
        (
          'Archiver l’environnement '
          + `« ${environment.name} » ?`
          + '\n\n'
          + 'Il ne sera plus proposé '
          + 'aux nouveaux projets.'
        ),
      );


    if (!confirmed) {
      return;
    }


    this.archivingEnvironmentId.set(
      environment.id
    );


    this.clearMessages();


    this.infrastructureService
      .archiveEnvironment(
        environment.id
      )
      .pipe(
        finalize(
          () =>
            this.archivingEnvironmentId
              .set(
                null
              ),
        ),
      )
      .subscribe({
        next: (
          archived:
            ArchivedEnvironment,
        ) => {
          this.selectedEnvironmentId.set(
            null
          );


          this.successMessage.set(
            (
              'L’environnement '
              + `« ${archived.name} » `
              + 'a été archivé.'
            ),
          );


          this.loadOverview();
        },


        error: (
          error:
            HttpErrorResponse,
        ) => {
          this.errorMessage.set(
            this.resolveError(
              error
            ),
          );
        },
      });
  }


  connectionsForRole(
    definition:
      ServiceDefinition,
  ): AvailableConnection[] {
    const currentOverview =
      this.overview();


    if (!currentOverview) {
      return [];
    }


    return currentOverview.connections
      .filter(
        (
          connection:
            AvailableConnection,
        ) =>
          definition.providers.includes(
            connection.providerType,
          ),
      )
      .sort(
        (
          first:
            AvailableConnection,

          second:
            AvailableConnection,
        ) => {
          const statusOrder:
            Record<
              IntegrationStatus,
              number
            > = {
              online:
                0,

              degraded:
                1,

              unchecked:
                2,

              not_configured:
                3,

              offline:
                4,
            };


          return (
            statusOrder[
              first.status
            ]

            - statusOrder[
              second.status
            ]

            || first.name.localeCompare(
              second.name
            )
          );
        },
      );
  }


  serviceForRole(
    environment:
      DeploymentEnvironment,

    role:
      ServiceRole,
  ): EnvironmentService | null {
    return (
      environment.services.find(
        (
          service:
            EnvironmentService,
        ) =>
          service.role === role,
      )

      ?? null
    );
  }


  environmentTypeLabel(
    type:
      EnvironmentType,
  ): string {
    const labels:
      Record<
        EnvironmentType,
        string
      > = {
        lab:
          'Lab',

        staging:
          'Staging',

        production:
          'Production',

        custom:
          'Personnalisé',
      };


    return labels[type];
  }


  environmentStatusLabel(
    status:
      EnvironmentStatus,
  ): string {
    const labels:
      Record<
        EnvironmentStatus,
        string
      > = {
        draft:
          'À compléter',

        ready:
          'Opérationnel',

        degraded:
          'Dégradé',

        offline:
          'Hors ligne',

        archived:
          'Archivé',
      };


    return labels[status];
  }


  integrationStatusLabel(
    status:
      IntegrationStatus,
  ): string {
    const labels:
      Record<
        IntegrationStatus,
        string
      > = {
        not_configured:
          'Non configuré',

        unchecked:
          'Non testé',

        online:
          'Opérationnel',

        degraded:
          'Dégradé',

        offline:
          'Hors ligne',
      };


    return labels[status];
  }


  providerLabel(
    provider:
      InfrastructureProviderType,
  ): string {
    const labels:
      Record<
        InfrastructureProviderType,
        string
      > = {
        gitlab:
          'GitLab',

        github:
          'GitHub',

        nexus:
          'Nexus Repository',

        argocd:
          'Argo CD',

        kubernetes:
          'Kubernetes',

        nfs:
          'NFS',

        ollama:
          'Ollama',

        litellm:
          'LiteLLM',

        vllm:
          'vLLM',

        openai_compatible:
          'API compatible OpenAI',

        generic_http:
          'Service HTTP personnalisé',
      };


    return labels[provider];
  }


  private buildRequest():
    SaveEnvironmentRequest {
    const values =
      this.environmentForm
        .getRawValue();


    const connectionIds:
      Partial<
        Record<
          ServiceRole,
          number
        >
      > = {};


    for (
      const definition
      of this.serviceDefinitions
    ) {
      const connectionId =
        values[
          definition.controlName
        ];


      if (connectionId > 0) {
        connectionIds[
          definition.role
        ] = connectionId;
      }
    }


    return {
      name:
        values.name.trim(),

      environmentType:
        values.environmentType,

      namespace:
        values.namespace.trim(),

      domain:
        values.domain.trim()
        || null,

      description:
        values.description.trim()
        || null,

      connectionIds,
    };
  }


  private connectionValuesFromEnvironment(
    environment:
      DeploymentEnvironment,
  ): Record<
    ConnectionControlName,
    number
  > {
    const values:
      Record<
        ConnectionControlName,
        number
      > = {
        kubernetesConnectionId:
          0,

        argocdConnectionId:
          0,

        registryConnectionId:
          0,

        gitopsConnectionId:
          0,

        storageConnectionId:
          0,

        aiConnectionId:
          0,

        httpServiceConnectionId:
          0,
      };


    for (
      const definition
      of this.serviceDefinitions
    ) {
      values[
        definition.controlName
      ] = (
        this.serviceForRole(
          environment,
          definition.role,
        )?.connectionId

        ?? 0
      );
    }


    return values;
  }


  private clearMessages():
    void {
    this.errorMessage.set(
      null
    );

    this.successMessage.set(
      null
    );
  }


  private resolveError(
    error:
      HttpErrorResponse,
  ): string {
    if (error.status === 0) {
      return (
        'Le backend Flask est inaccessible. '
        + 'Vérifiez le port 5000 '
        + 'et le proxy Angular.'
      );
    }


    const response =
      error.error as
        ApiErrorResponse | null;


    if (
      response?.error?.message
    ) {
      return response.error.message;
    }


    return (
      'Erreur HTTP '
      + `${error.status || 'inconnue'}.`
    );
  }
}