import { HttpErrorResponse } from '@angular/common/http';
import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { finalize, forkJoin } from 'rxjs';

import {
  CredentialSource,
  GitTokenType,
  GitTransport,
  Project,
  ProjectEnvironmentOption,
  ProjectOperationMode,
  ProjectOptions,
  ProjectSourceType,
  ProjectsService,
  RepositoryVisibility,
  SourceAuthMethod,
  SourceValidationResult,
  ValidateGitSourceRequest,
} from '../../../../core/projects/projects';

interface ApiErrorResponse {
  success: false;
  error: { code: string; message: string };
}

type CreationStep = 1 | 2 | 3 | 4;

@Component({
  selector: 'app-new-project',
  imports: [ReactiveFormsModule, RouterLink],
  templateUrl: './new-project.html',
  styleUrl: './new-project.scss',
})
export class NewProject implements OnInit {
  private readonly projectsService = inject(ProjectsService);
  private readonly formBuilder = inject(FormBuilder);
  private readonly router = inject(Router);
  private readonly route = inject(ActivatedRoute);

  readonly step = signal<CreationStep>(1);
  readonly draftId = signal<number | null>(null);
  readonly draft = signal<Project | null>(null);
  readonly options = signal<ProjectOptions | null>(null);
  readonly sourceValidation = signal<SourceValidationResult | null>(null);
  readonly selectedArchiveFile = signal<File | null>(null);
  readonly selectedEnvironmentId = signal<number | null>(null);

  readonly isLoading = signal(true);
  readonly isSaving = signal(false);
  readonly isTesting = signal(false);
  readonly pageError = signal<string | null>(null);
  readonly stepError = signal<string | null>(null);
  readonly successMessage = signal<string | null>(null);

  readonly identityForm = this.formBuilder.nonNullable.group({
    operationMode: this.formBuilder.nonNullable.control<ProjectOperationMode>('new_application'),
    name: ['', [Validators.required, Validators.minLength(3), Validators.maxLength(140)]],
    description: ['', [Validators.maxLength(1000)]],
  });

  readonly sourceForm = this.formBuilder.nonNullable.group({
    sourceType: this.formBuilder.nonNullable.control<ProjectSourceType>('git'),
    sourceConnectionId: [0],
    repositoryUrl: [''],
    branch: ['main'],
    sourceSubdirectory: [''],
    visibility: this.formBuilder.nonNullable.control<RepositoryVisibility>('private'),
    transport: this.formBuilder.nonNullable.control<GitTransport>('https'),
    credentialSource: this.formBuilder.nonNullable.control<CredentialSource>('project'),
    authMethod: this.formBuilder.nonNullable.control<SourceAuthMethod>('https_token'),
    tokenType: this.formBuilder.nonNullable.control<GitTokenType>('project_access_token'),
    username: ['oauth2'],
    secret: [''],
  });

  readonly environments = computed<ProjectEnvironmentOption[]>(
    () => this.options()?.environments ?? [],
  );

  readonly selectedConnection = computed(() => {
    const id = Number(this.sourceForm.controls.sourceConnectionId.value);
    return this.options()?.gitConnections.find(connection => connection.id === id) ?? null;
  });

  readonly selectedEnvironment = computed(() => {
    const id = this.selectedEnvironmentId();
    return this.environments().find(environment => environment.id === id) ?? null;
  });

  readonly credentialAlreadyStored = computed(
    () => this.draft()?.source.credentialConfigured === true,
  );

  readonly archiveLimitMegabytes = computed(
    () => this.options()?.archiveLimits?.maxMegabytes || 0,
  );

  readonly sourceReviewLabel = computed(() => {
    if (this.sourceForm.controls.sourceType.value === 'git') {
      return this.sourceForm.controls.repositoryUrl.value || 'Repository Git';
    }

    return this.selectedArchiveFile()?.name
      || this.draft()?.source.archive?.originalName
      || 'Archive ZIP';
  });

  readonly stepItems = [
    { number: 1, label: 'Informations', description: 'Identité du projet' },
    { number: 2, label: 'Source', description: 'Repository et credential' },
    { number: 3, label: 'Environnement', description: 'Infrastructure cible' },
    { number: 4, label: 'Vérification', description: 'Résumé et activation' },
  ];

  ngOnInit(): void {
    const draftId = Number(this.route.snapshot.queryParamMap.get('draftId'));
    const requestedStep = Number(this.route.snapshot.queryParamMap.get('step'));

    if (Number.isInteger(draftId) && draftId > 0) {
      this.draftId.set(draftId);
    }
    if ([1, 2, 3, 4].includes(requestedStep)) {
      this.step.set(requestedStep as CreationStep);
    }

    this.loadInitialData();
  }

  selectOperationMode(mode: ProjectOperationMode): void {
    this.identityForm.controls.operationMode.setValue(mode);
  }

  selectSourceType(type: ProjectSourceType): void {
    this.sourceForm.controls.sourceType.setValue(type);
    this.sourceValidation.set(null);
    this.successMessage.set(null);
    this.stepError.set(null);
  }

  selectEnvironment(environmentId: number): void {
    this.selectedEnvironmentId.set(environmentId);
    this.stepError.set(null);
  }

  onArchiveSelected(event: Event): void {
    const input = event.target as HTMLInputElement;
    this.selectedArchiveFile.set(input.files?.item(0) ?? null);
    this.sourceValidation.set(null);
  }

  goToStep(step: CreationStep): void {
    if (step > this.highestUnlockedStep()) {
      return;
    }
    this.step.set(step);
    this.syncUrl();
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  saveIdentityAndContinue(): void {
    this.identityForm.markAllAsTouched();
    this.stepError.set(null);

    if (this.identityForm.invalid) {
      this.stepError.set('Saisissez un nom de projet valide.');
      return;
    }

    const values = this.identityForm.getRawValue();
    this.isSaving.set(true);

    const existingDraftId = this.draftId();
    const request$ = existingDraftId
      ? this.projectsService.getProject(existingDraftId)
      : this.projectsService.createDraft({
          operationMode: values.operationMode,
          name: values.name.trim(),
          description: values.description.trim() || null,
        });

    request$
      .pipe(finalize(() => this.isSaving.set(false)))
      .subscribe({
        next: project => {
          this.draft.set(project);
          this.draftId.set(project.id);
          this.step.set(2);
          this.syncUrl();
        },
        error: error => this.stepError.set(this.resolveError(error)),
      });
  }

  saveSourceAndContinue(): void {
    const projectId = this.draftId();
    if (!projectId) {
      this.stepError.set('Le brouillon du projet doit être créé avant la source.');
      return;
    }

    const request = this.buildSourceRequest();
    if (!request) {
      return;
    }

    this.isSaving.set(true);
    this.stepError.set(null);
    this.successMessage.set(null);

    this.projectsService
      .saveDraftSource(projectId, request)
      .pipe(finalize(() => this.isSaving.set(false)))
      .subscribe({
        next: result => {
          this.draft.set(result.project);
          this.sourceValidation.set(result.sourceValidation);
          this.successMessage.set('La source et son credential ont été enregistrés de manière chiffrée.');
          this.step.set(3);
          this.syncUrl();
        },
        error: error => this.stepError.set(this.resolveError(error)),
      });
  }

  testStoredSource(): void {
    const projectId = this.draftId();
    if (!projectId) {
      return;
    }

    this.isTesting.set(true);
    this.stepError.set(null);
    this.successMessage.set(null);

    this.projectsService
      .testStoredSource(projectId)
      .pipe(finalize(() => this.isTesting.set(false)))
      .subscribe({
        next: validation => {
          this.sourceValidation.set(validation);
          this.successMessage.set('La source enregistrée est accessible avec le credential stocké.');
        },
        error: error => this.stepError.set(this.resolveError(error)),
      });
  }

  saveEnvironmentAndContinue(): void {
    const projectId = this.draftId();
    const environmentId = this.selectedEnvironmentId();

    if (!projectId || environmentId === null) {
      this.stepError.set('Sélectionnez un environnement avant de continuer.');
      return;
    }

    this.isSaving.set(true);
    this.stepError.set(null);

    this.projectsService
      .saveProjectEnvironment(projectId, { environmentId })
      .pipe(finalize(() => this.isSaving.set(false)))
      .subscribe({
        next: project => {
          this.draft.set(project);
          this.step.set(4);
          this.syncUrl();
        },
        error: error => this.stepError.set(this.resolveError(error)),
      });
  }

  activateAndAnalyze(): void {
    const projectId = this.draftId();
    if (!projectId) {
      return;
    }

    this.isSaving.set(true);
    this.stepError.set(null);

    this.projectsService
      .activateProject(projectId)
      .pipe(finalize(() => this.isSaving.set(false)))
      .subscribe({
        next: project => {
          void this.router.navigate(['/projects', project.id, 'analysis']);
        },
        error: error => this.stepError.set(this.resolveError(error)),
      });
  }

  highestUnlockedStep(): CreationStep {
    if (!this.draftId()) {
      return 1;
    }
    if (!this.draft()?.source.repositoryUrl && this.draft()?.source.type !== 'zip') {
      return 2;
    }
    if (!this.draft()?.defaultEnvironment) {
      return 3;
    }
    return 4;
  }

  formatBytes(value: number | null | undefined): string {
    if (!value) {
      return '0 o';
    }
    if (value < 1024) {
      return `${value} o`;
    }
    if (value < 1024 * 1024) {
      return `${(value / 1024).toFixed(1)} Ko`;
    }
    return `${(value / 1024 / 1024).toFixed(1)} Mo`;
  }

  private loadInitialData(): void {
    this.isLoading.set(true);
    this.pageError.set(null);

    const draftId = this.draftId();
    const options$ = this.projectsService.getOptions();
    if (draftId) {
      forkJoin({
        options: options$,
        project: this.projectsService.getProject(draftId),
      })
        .pipe(finalize(() => this.isLoading.set(false)))
        .subscribe({
          next: result => {
            this.applyOptions(result.options);
            this.applyDraft(result.project);
          },
          error: error => this.pageError.set(this.resolveError(error)),
        });

      return;
    }

    options$
      .pipe(finalize(() => this.isLoading.set(false)))
      .subscribe({
        next: options => this.applyOptions(options),
        error: error => this.pageError.set(this.resolveError(error)),
      });
  }

  private applyOptions(options: ProjectOptions): void {
    this.options.set(options);
    const firstConnection = options.gitConnections[0];
    if (firstConnection && this.sourceForm.controls.sourceConnectionId.value === 0) {
      this.sourceForm.controls.sourceConnectionId.setValue(firstConnection.id);
    }
  }

  private applyDraft(project: Project): void {
    this.draft.set(project);
    this.draftId.set(project.id);
    this.identityForm.patchValue({
      operationMode: project.operationMode,
      name: project.name,
      description: project.description ?? '',
    });
    this.sourceForm.patchValue({
      sourceType: project.source.type,
      sourceConnectionId: project.source.connectionId ?? 0,
      repositoryUrl: project.source.repositoryUrl ?? '',
      branch: project.source.branch || 'main',
      sourceSubdirectory: project.source.subdirectory ?? '',
      visibility: project.source.visibility,
      transport: project.source.transport === 'archive' ? 'https' : project.source.transport,
      credentialSource: project.source.credentialSource,
      authMethod: project.source.authMethod,
      tokenType: project.source.tokenType ?? 'project_access_token',
      username: project.source.username ?? 'oauth2',
      secret: '',
    });
    this.selectedEnvironmentId.set(project.defaultEnvironment?.id ?? null);
  }

  private buildSourceRequest(): ValidateGitSourceRequest | FormData | null {
    if (this.sourceForm.controls.sourceType.value === 'zip') {
      return this.buildZipFormData();
    }

    const values = this.sourceForm.getRawValue();
    if (Number(values.sourceConnectionId) <= 0) {
      this.stepError.set('Sélectionnez une connexion Git.');
      return null;
    }
    if (!values.repositoryUrl.trim() || !values.branch.trim()) {
      this.stepError.set('L’URL du repository et la branche sont obligatoires.');
      return null;
    }

    const isPrivate = values.visibility === 'private';
    const credentialSource: CredentialSource = isPrivate ? values.credentialSource : 'none';
    const authMethod: SourceAuthMethod = isPrivate ? values.authMethod : 'none';
    const existingCredential = this.credentialAlreadyStored();
    const secret = values.secret.trim() || null;

    if (isPrivate && credentialSource === 'project' && !secret && !existingCredential) {
      this.stepError.set('Saisissez le token ou la clé privée du projet.');
      return null;
    }
    if (isPrivate && values.transport === 'https' && !values.username.trim()) {
      this.stepError.set('Le username Git est obligatoire pour HTTPS.');
      return null;
    }

    return {
      sourceType: 'git',
      sourceConnectionId: Number(values.sourceConnectionId),
      repositoryUrl: values.repositoryUrl.trim(),
      visibility: values.visibility,
      transport: values.transport,
      credentialSource,
      authMethod,
      tokenType: authMethod === 'https_token' ? values.tokenType : null,
      username: isPrivate ? values.username.trim() || null : null,
      secret,
      branch: values.branch.trim(),
      sourceSubdirectory: values.sourceSubdirectory.trim() || null,
    };
  }

  private buildZipFormData(): FormData | null {
    const file = this.selectedArchiveFile();
    if (!file) {
      this.stepError.set('Sélectionnez une archive ZIP.');
      return null;
    }
    const maxBytes = this.options()?.archiveLimits.maxBytes;
    if (!file.name.toLowerCase().endsWith('.zip') || (maxBytes && file.size > maxBytes)) {
      this.stepError.set('L’archive ZIP est invalide ou dépasse la taille autorisée.');
      return null;
    }

    const formData = new FormData();
    formData.append('payload', JSON.stringify({
      sourceType: 'zip',
      sourceSubdirectory: this.sourceForm.controls.sourceSubdirectory.value.trim() || null,
    }));
    formData.append('archiveFile', file, file.name);
    return formData;
  }

  private syncUrl(): void {
    void this.router.navigate([], {
      relativeTo: this.route,
      queryParams: {
        draftId: this.draftId(),
        step: this.step(),
      },
      queryParamsHandling: 'merge',
      replaceUrl: true,
    });
  }

  private resolveError(error: HttpErrorResponse): string {
    if (error.status === 0) {
      return 'Le backend Flask est inaccessible.';
    }
    const response = error.error as ApiErrorResponse | null;
    return response?.error?.message || `Erreur HTTP ${error.status}.`;
  }
}