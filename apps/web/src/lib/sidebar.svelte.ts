// Sidebar collapse state (icon-rail vs full), persisted like the theme preference.
import { browser } from '$app/environment';

const KEY = 'dn42.sidebar.collapsed';

class Sidebar {
	collapsed = $state(browser ? localStorage.getItem(KEY) === '1' : false);

	toggle() {
		this.set(!this.collapsed);
	}

	set(v: boolean) {
		this.collapsed = v;
		if (browser) localStorage.setItem(KEY, v ? '1' : '0');
	}
}

export const sidebar = new Sidebar();
