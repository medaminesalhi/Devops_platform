import { Component, Input } from '@angular/core';
import { RouterLink } from '@angular/router';

export type ProjectPhaseKey =
  | 'configuration'
  | 'analysis'
  | 'proposal'
  | 'generation'
  | 'deployment';

export interface ProjectPhaseItem {
  key: ProjectPhaseKey;
  number: number;
  label: string;
  description: string;
  path: string;
  completed: boolean;
  unlocked: boolean;
}

@Component({
  selector: 'app-project-phase-stepper',
  imports: [RouterLink],
  templateUrl: './project-phase-stepper.html',
  styleUrl: './project-phase-stepper.scss',
})
export class ProjectPhaseStepper {
  @Input({ required: true }) phases: ProjectPhaseItem[] = [];
  @Input({ required: true }) activePhase!: ProjectPhaseKey;
}