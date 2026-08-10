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
  CATEGORY_DEFINITIONS,
  IntegrationCategory,
  PROVIDER_DEFINITIONS,
  ProviderDefinition,
  buildCheckedUrl,
  providersForCategory,
  resolveUrlDetails,
} from '../../../../core/integrations/integration-provider-catalog';

import {
  AuthenticationType,
  ConnectionStatus,
  DeletedConnectionResult,
  IntegrationConfiguration,
  IntegrationConnection,
  IntegrationRepositoryOption,
  IntegrationTestResult,
  IntegrationsService,
  ProviderType,
  SavedConnectionResult,
} from '../../../../core/integrations/integrations';


interface ApiErrorResponse {
  success: false;

  error: {
    code: string;
    message: string;
  };
}


@Component({
  selector: 'app-integrations',

  imports: [
    ReactiveFormsModule,
    DatePipe,
  ],

  templateUrl:
    './integrations.html',

  styleUrl:
    './integrations.scss',
})
export class Integrations
  implements OnInit {
  private readonly integrationsService =
    inject(IntegrationsService);

  private readonly formBuilder =
    inject(FormBuilder);


  readonly categoryDefinitions =
    CATEGORY_DEFINITIONS;


  readonly connections =
    signal<
      IntegrationConnection[]
    >([]);


  readonly selectedConnectionId =
    signal<number | null>(null);


  readonly editorOpen =
    signal(false);


  readonly editingConnectionId =
    signal<number | null>(null);


  readonly isLoading =
    signal(true);


  readonly isSaving =
    signal(false);


  readonly isTestingDraft =
    signal(false);


  readonly testingConnectionId =
    signal<number | null>(null);


  readonly deletingConnectionId =
    signal<number | null>(null);


  readonly errorMessage =
    signal<string | null>(null);


  readonly successMessage =
    signal<string | null>(null);


  readonly draftTestResult =
    signal<
      IntegrationTestResult | null
    >(null);


  readonly savedTestResult =
    signal<
      IntegrationTestResult | null
    >(null);


  readonly draftRepositories =
    signal<IntegrationRepositoryOption[]>([]);


  readonly savedRepositories =
    signal<IntegrationRepositoryOption[]>([]);


  readonly isDiscoveringRepositories =
    signal(false);


  readonly repositoryDiscoveryError =
    signal<string | null>(null);


  readonly selectedCategory =
    signal<
      IntegrationCategory | null
    >(null);


  readonly selectedProviderType =
    signal<
      ProviderType | null
    >(null);


  readonly currentBaseUrl =
    signal('');


  readonly connectionForm =
    this.formBuilder
      .nonNullable
      .group({
        category: [
          '' as
            IntegrationCategory | '',

          [
            Validators.required,
          ],
        ],

        providerType: [
          '' as ProviderType | '',

          [
            Validators.required,
          ],
        ],

        name: [
          '',

          [
            Validators.required,
            Validators.maxLength(120),
          ],
        ],

        baseUrl: [
          '',

          [
            Validators.required,

            Validators.pattern(
              /^(https?|nfs):\/\/.+/i,
            ),
          ],
        ],

        description: [
          '',

          [
            Validators.maxLength(500),
          ],
        ],

        authType: [
          'none' as AuthenticationType,

          [
            Validators.required,
          ],
        ],

        username: [
          '',

          [
            Validators.maxLength(200),
          ],
        ],

        credential: [
          '',

          [
            Validators.maxLength(5000),
          ],
        ],

        verifySsl: [
          true,
        ],

        monitoringEnabled: [
          true,
        ],

        checkIntervalSeconds: [
          300,

          [
            Validators.required,
            Validators.min(60),
            Validators.max(86400),
          ],
        ],

        failureThreshold: [
          3,

          [
            Validators.required,
            Validators.min(1),
            Validators.max(10),
          ],
        ],
      });


  readonly selectedConnection =
    computed(
      ():
        IntegrationConnection
        | null => {
        const selectedId =
          this.selectedConnectionId();

        return (
          this.connections()
            .find(
              (
                connection:
                  IntegrationConnection,
              ) =>
                connection.id
                === selectedId,
            )
          ?? null
        );
      },
    );


  readonly providerOptions =
    computed(
      ():
        ProviderDefinition[] =>
          providersForCategory(
            this.selectedCategory(),
          ),
    );


  readonly currentProvider =
    computed(
      ():
        ProviderDefinition | null => {
        const providerType =
          this.selectedProviderType();

        return providerType
          ? PROVIDER_DEFINITIONS[
              providerType
            ]
          : null;
      },
    );


  readonly checkedUrl =
    computed(
      (): string => {
        const providerType =
          this.selectedProviderType();

        if (!providerType) {
          return '';
        }

        return buildCheckedUrl(
          providerType,
          this.currentBaseUrl(),
        );
      },
    );


  readonly urlDetails =
    computed(
      () =>
        resolveUrlDetails(
          this.currentBaseUrl(),
        ),
    );


  readonly onlineCount =
    computed(
      () =>
        this.connections()
          .filter(
            (
              connection:
                IntegrationConnection,
            ) =>
              connection.status
              === 'online',
          )
          .length,
    );


  readonly degradedCount =
    computed(
      () =>
        this.connections()
          .filter(
            (
              connection:
                IntegrationConnection,
            ) =>
              connection.status
              === 'degraded',
          )
          .length,
    );


  readonly offlineCount =
    computed(
      () =>
        this.connections()
          .filter(
            (
              connection:
                IntegrationConnection,
            ) =>
              connection.status
              === 'offline',
          )
          .length,
    );


  ngOnInit(): void {
    this.loadConnections();
  }


  loadConnections(): void {
    this.isLoading.set(true);
    this.errorMessage.set(null);

    this.integrationsService
      .getAll()
      .pipe(
        finalize(
          () =>
            this.isLoading.set(
              false,
            ),
        ),
      )
      .subscribe({
        next: (
          connections:
            IntegrationConnection[],
        ) => {
          this.connections.set(
            connections
          );

          const currentSelection =
            this.selectedConnectionId();

          const selectionStillExists =
            connections.some(
              (
                connection:
                  IntegrationConnection,
              ) =>
                connection.id
                === currentSelection,
            );

          if (!selectionStillExists) {
            this.selectedConnectionId
              .set(
                connections[0]?.id
                ?? null,
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


  selectConnection(
    connection:
      IntegrationConnection,
  ): void {
    this.selectedConnectionId.set(
      connection.id,
    );

    this.savedTestResult.set(
      null,
    );

    this.savedRepositories.set([]);
    this.repositoryDiscoveryError.set(null);
    this.discoverSavedRepositories(connection);
  }


  openCreateEditor(): void {
    this.editingConnectionId.set(
      null,
    );

    this.selectedCategory.set(
      null,
    );

    this.selectedProviderType.set(
      null,
    );

    this.currentBaseUrl.set('');

    this.connectionForm.reset({
      category: '',
      providerType: '',
      name: '',
      baseUrl: '',
      description: '',
      authType: 'none',
      username: '',
      credential: '',
      verifySsl: true,
      monitoringEnabled: true,
      checkIntervalSeconds: 300,
      failureThreshold: 3,
    });

    this.clearEditorMessages();
    this.draftRepositories.set([]);
    this.repositoryDiscoveryError.set(null);

    this.editorOpen.set(true);
  }


  openEditEditor(
    connection:
      IntegrationConnection,
  ): void {
    const definition =
      PROVIDER_DEFINITIONS[
        connection.providerType
      ];

    this.editingConnectionId.set(
      connection.id,
    );

    this.selectedCategory.set(
      definition.category,
    );

    this.selectedProviderType.set(
      connection.providerType,
    );

    this.currentBaseUrl.set(
      connection.baseUrl,
    );

    this.connectionForm.reset({
      category:
        definition.category,

      providerType:
        connection.providerType,

      name:
        connection.name,

      baseUrl:
        connection.baseUrl,

      description:
        connection.description
        ?? '',

      authType:
        connection.authType,

      username:
        connection.username
        ?? '',

      credential: '',

      verifySsl:
        connection.verifySsl,

      monitoringEnabled:
        connection
          .monitoringEnabled,

      checkIntervalSeconds:
        connection
          .checkIntervalSeconds,

      failureThreshold:
        connection
          .failureThreshold,
    });

    this.clearEditorMessages();
    this.draftRepositories.set([]);
    this.repositoryDiscoveryError.set(null);

    this.editorOpen.set(true);

    if (connection.providerType === 'nexus' || connection.providerType === 'gitlab') {
      this.discoverSavedRepositories(connection, true);
    }
  }


  closeEditor(): void {
    if (
      this.isSaving()
      || this.isTestingDraft()
    ) {
      return;
    }

    this.editorOpen.set(false);
    this.draftTestResult.set(null);
  }


  onCategoryChanged(): void {
    const rawCategory =
      this.connectionForm.controls
        .category.value;

    const category =
      rawCategory
        ? rawCategory as IntegrationCategory
        : null;

    this.selectedCategory.set(
      category,
    );

    this.selectedProviderType.set(
      null,
    );

    this.currentBaseUrl.set('');

    this.connectionForm.patchValue({
      providerType: '',
      name: '',
      baseUrl: '',
      authType: 'none',
      username: '',
      credential: '',
      verifySsl: true,
    });

    this.draftTestResult.set(null);
    this.draftRepositories.set([]);
    this.repositoryDiscoveryError.set(null);
  }


  onProviderChanged(): void {
    const rawProvider =
      this.connectionForm.controls
        .providerType.value;

    if (!rawProvider) {
      this.selectedProviderType.set(
        null,
      );

      return;
    }

    const providerType =
      rawProvider as ProviderType;

    const definition =
      PROVIDER_DEFINITIONS[
        providerType
      ];

    this.selectedProviderType.set(
      providerType,
    );

    this.currentBaseUrl.set('');

    this.connectionForm.patchValue({
      name: '',
      baseUrl: '',

      authType:
        definition.defaultAuthType,

      username: '',
      credential: '',
      verifySsl: true,
    });

    this.draftTestResult.set(null);
    this.draftRepositories.set([]);
    this.repositoryDiscoveryError.set(null);
  }


  onAuthTypeChanged(): void {
    this.connectionForm.patchValue({
      username: '',
      credential: '',
    });

    this.draftTestResult.set(null);
  }


  onBaseUrlChanged(): void {
    this.currentBaseUrl.set(
      this.connectionForm.controls
        .baseUrl.value,
    );

    this.draftTestResult.set(null);
  }


  saveConnection(): void {
    this.connectionForm
      .markAllAsTouched();

    if (
      this.connectionForm.invalid
      || this.isSaving()
    ) {
      return;
    }

    const configuration =
      this.buildConfiguration();

    const editingId =
      this.editingConnectionId();

    this.isSaving.set(true);
    this.errorMessage.set(null);
    this.successMessage.set(null);
    this.savedTestResult.set(null);

    const request$ =
      editingId === null
        ? this.integrationsService
            .create(configuration)

        : this.integrationsService
            .update(
              editingId,
              configuration,
            );

    request$
      .pipe(
        finalize(
          () =>
            this.isSaving.set(
              false,
            ),
        ),
      )
      .subscribe({
        next: (
          result:
            SavedConnectionResult,
        ) => {
          this.upsertConnection(
            result.connection,
          );

          this.selectedConnectionId.set(
            result.connection.id,
          );

          this.savedTestResult.set(
            result.test,
          );

          this.editorOpen.set(false);

          this.successMessage.set(
            editingId === null
              ? (
                  'Connexion créée et '
                  + 'testée automatiquement.'
                )
              : (
                  'Connexion modifiée et '
                  + 'testée automatiquement.'
                ),
          );

          if (result.testError) {
            this.errorMessage.set(
              (
                'La configuration est '
                + 'enregistrée, mais le '
                + 'test automatique a '
                + 'échoué : '
                + result.testError
              ),
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


  testDraftConfiguration(): void {
    this.connectionForm
      .markAllAsTouched();

    if (
      this.connectionForm.invalid
      || this.isTestingDraft()
    ) {
      return;
    }

    const configuration =
      this.buildConfiguration();

    const editingId =
      this.editingConnectionId();

    if (editingId !== null) {
      configuration.connectionId =
        editingId;
    }

    this.isTestingDraft.set(true);
    this.draftTestResult.set(null);
    this.errorMessage.set(null);

    this.integrationsService
      .testDraft(configuration)
      .pipe(
        finalize(
          () =>
            this.isTestingDraft.set(
              false,
            ),
        ),
      )
      .subscribe({
        next: (
          result:
            IntegrationTestResult,
        ) => {
          this.draftTestResult.set(
            result,
          );

          if (result.server_reachable) {
            this.discoverDraftRepositories(configuration);
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


  testSavedConnection(
    connection:
      IntegrationConnection,
  ): void {
    if (
      this.testingConnectionId()
      !== null
    ) {
      return;
    }

    this.testingConnectionId.set(
      connection.id,
    );

    this.errorMessage.set(null);
    this.savedTestResult.set(null);

    this.integrationsService
      .testSaved(connection.id)
      .pipe(
        finalize(
          () =>
            this.testingConnectionId
              .set(null),
        ),
      )
      .subscribe({
        next: (result) => {
          this.upsertConnection(
            result.connection,
          );

          this.selectedConnectionId.set(
            result.connection.id,
          );

          this.savedTestResult.set(
            result.test,
          );

          this.discoverSavedRepositories(result.connection);
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


  private discoverDraftRepositories(
    configuration: IntegrationConfiguration,
  ): void {
    if (!['nexus', 'gitlab'].includes(configuration.providerType)) {
      this.draftRepositories.set([]);
      return;
    }

    this.isDiscoveringRepositories.set(true);
    this.repositoryDiscoveryError.set(null);

    this.integrationsService
      .discoverDraftRepositories(configuration)
      .pipe(finalize(() => this.isDiscoveringRepositories.set(false)))
      .subscribe({
        next: repositories => this.draftRepositories.set(repositories),
        error: (error: HttpErrorResponse) => {
          this.draftRepositories.set([]);
          this.repositoryDiscoveryError.set(this.resolveError(error));
        },
      });
  }


  private discoverSavedRepositories(
    connection: IntegrationConnection,
    copyToDraft = false,
  ): void {
    if (!['nexus', 'gitlab'].includes(connection.providerType)) {
      this.savedRepositories.set([]);
      if (copyToDraft) this.draftRepositories.set([]);
      return;
    }

    this.isDiscoveringRepositories.set(true);
    this.repositoryDiscoveryError.set(null);

    this.integrationsService
      .discoverSavedRepositories(connection.id)
      .pipe(finalize(() => this.isDiscoveringRepositories.set(false)))
      .subscribe({
        next: repositories => {
          this.savedRepositories.set(repositories);
          if (copyToDraft) this.draftRepositories.set(repositories);
        },
        error: (error: HttpErrorResponse) => {
          this.savedRepositories.set([]);
          if (copyToDraft) this.draftRepositories.set([]);
          this.repositoryDiscoveryError.set(this.resolveError(error));
        },
      });
  }


  deleteConnection(
    connection:
      IntegrationConnection,
  ): void {
    if (
      this.deletingConnectionId()
      !== null
    ) {
      return;
    }

    const confirmed =
      window.confirm(
        (
          'Supprimer la connexion '
          + `« ${connection.name} » ?`
          + '\n\n'
          + 'Son credential et son '
          + 'historique de santé '
          + 'seront également supprimés.'
        ),
      );

    if (!confirmed) {
      return;
    }

    this.deletingConnectionId.set(
      connection.id,
    );

    this.errorMessage.set(null);
    this.successMessage.set(null);

    this.integrationsService
      .delete(connection.id)
      .pipe(
        finalize(
          () =>
            this.deletingConnectionId
              .set(null),
        ),
      )
      .subscribe({
        next: (
          result:
            DeletedConnectionResult,
        ) => {
          this.connections.update(
            (
              connections:
                IntegrationConnection[],
            ) =>
              connections.filter(
                (
                  current:
                    IntegrationConnection,
                ) =>
                  current.id
                  !== result.id,
              ),
          );

          if (
            this.selectedConnectionId()
            === result.id
          ) {
            this.selectedConnectionId.set(
              this.connections()[0]?.id
              ?? null,
            );
          }

          this.savedTestResult.set(
            null,
          );

          this.successMessage.set(
            (
              'La connexion '
              + `« ${result.name} » `
              + 'a été supprimée.'
            ),
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


  requiresUsername(): boolean {
    return (
      this.connectionForm.controls
        .authType.value
      === 'basic'
    );
  }


  requiresCredential(): boolean {
    return (
      this.connectionForm.controls
        .authType.value
      !== 'none'
    );
  }


  isEditing(): boolean {
    return (
      this.editingConnectionId()
      !== null
    );
  }


  providerLabel(
    provider: ProviderType,
  ): string {
    return (
      PROVIDER_DEFINITIONS[
        provider
      ].label
    );
  }


  providerEndpoint(
    provider: ProviderType,
  ): string {
    const definition =
      PROVIDER_DEFINITIONS[
        provider
      ];

    return definition.exactUrl
      ? 'URL exacte'
      : definition.endpointPath;
  }


  statusLabel(
    status: ConnectionStatus,
  ): string {
    const labels:
      Record<
        ConnectionStatus,
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


  authTypeLabel(
    authType:
      AuthenticationType,
  ): string {
    const labels:
      Record<
        AuthenticationType,
        string
      > = {
        none:
          'Aucune',

        token:
          'Token / clé API',

        basic:
          (
            'Nom d’utilisateur '
            + 'et mot de passe'
          ),
      };

    return labels[authType];
  }


  intervalLabel(
    seconds: number,
  ): string {
    if (
      seconds % 3600 === 0
    ) {
      return (
        `${seconds / 3600} `
        + 'heure(s)'
      );
    }

    return (
      `${seconds / 60} `
      + 'minute(s)'
    );
  }


  private buildConfiguration():
    IntegrationConfiguration {
    const values =
      this.connectionForm
        .getRawValue();

    return {
      name:
        values.name.trim(),

      providerType:
        values.providerType as ProviderType,

      baseUrl:
        values.baseUrl.trim(),

      description:
        values.description.trim()
        || null,

      authType:
        values.authType,

      username:
        values.username.trim()
        || null,

      credential:
        values.credential.trim()
        || null,

      verifySsl:
        values.verifySsl,

      monitoringEnabled:
        values.monitoringEnabled,

      checkIntervalSeconds:
        values.checkIntervalSeconds,

      failureThreshold:
        values.failureThreshold,
    };
  }


  private clearEditorMessages():
    void {
    this.draftTestResult.set(null);
    this.errorMessage.set(null);
    this.successMessage.set(null);
  }


  private upsertConnection(
    updatedConnection:
      IntegrationConnection,
  ): void {
    this.connections.update(
      (
        connections:
          IntegrationConnection[],
      ) => {
        const exists =
          connections.some(
            (
              connection:
                IntegrationConnection,
            ) =>
              connection.id
              === updatedConnection.id,
          );

        const nextConnections =
          exists
            ? connections.map(
                (
                  connection:
                    IntegrationConnection,
                ) =>
                  connection.id
                  === updatedConnection.id
                    ? updatedConnection
                    : connection,
              )

            : [
                ...connections,
                updatedConnection,
              ];

        return nextConnections.sort(
          (
            first:
              IntegrationConnection,

            second:
              IntegrationConnection,
          ) =>
            first.name.localeCompare(
              second.name,
            ),
        );
      },
    );
  }


  private resolveError(
    error: HttpErrorResponse,
  ): string {
    if (error.status === 0) {
      return (
        'Le backend Flask est '
        + 'inaccessible. Vérifiez '
        + 'le service API et '
        + 'le proxy web.'
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