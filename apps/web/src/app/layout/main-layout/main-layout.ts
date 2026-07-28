import {
  Component,
  signal,
} from '@angular/core';

import {
  RouterOutlet,
} from '@angular/router';

import {
  Sidebar,
} from '../sidebar/sidebar';

import {
  Topbar,
} from '../topbar/topbar';


@Component({
  selector: 'app-main-layout',

  imports: [
    RouterOutlet,
    Sidebar,
    Topbar,
  ],

  templateUrl: './main-layout.html',
  styleUrl: './main-layout.scss',
})
export class MainLayout {
  /*
   * La sidebar est visible par défaut.
   */
  readonly sidebarOpen = signal(true);


  toggleSidebar(): void {
    this.sidebarOpen.update(
      (currentValue) => !currentValue,
    );
  }


  closeSidebarOnMobile(): void {
    /*
     * Sur un petit écran, la sidebar se ferme
     * lorsqu’une nouvelle page est affichée.
     */

    const isMobile =
      window.matchMedia(
        '(max-width: 900px)',
      ).matches;

    if (isMobile) {
      this.sidebarOpen.set(false);
    }
  }
}