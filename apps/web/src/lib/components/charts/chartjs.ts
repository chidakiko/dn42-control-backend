// Shared Chart.js setup: register only the pieces we use (tree-shaking), and helpers
// to bridge Chart.js to the app's CSS-variable theme (colours resolved at runtime so
// the same chart re-themes on light/dark/system switch when the component rebuilds it).
import {
	Chart,
	LineController,
	LineElement,
	PointElement,
	LinearScale,
	CategoryScale,
	BarController,
	BarElement,
	DoughnutController,
	ArcElement,
	Filler,
	Tooltip
} from 'chart.js';

let registered = false;

/** Register Chart.js controllers/elements once (idempotent). */
export function ensureChart(): void {
	if (registered) return;
	Chart.register(
		LineController,
		LineElement,
		PointElement,
		LinearScale,
		CategoryScale,
		BarController,
		BarElement,
		DoughnutController,
		ArcElement,
		Filler,
		Tooltip
	);
	registered = true;
}

/** Read a CSS custom property off :root (e.g. cssVar('--border')). */
export function cssVar(name: string): string {
	if (typeof document === 'undefined') return '';
	return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/** Resolve a `var(--x)` reference to its concrete value; pass through literals. */
export function resolveColor(color: string): string {
	const c = color.trim();
	if (c.startsWith('var(')) {
		const name = c.slice(4, -1).split(',')[0].trim();
		return cssVar(name) || c;
	}
	return c;
}

/** Normalise any colour (var/hex/name/rgb) to rgba() with the given alpha. */
export function withAlpha(color: string, alpha: number): string {
	const c = resolveColor(color);
	if (typeof document === 'undefined') return c;
	const ctx = document.createElement('canvas').getContext('2d');
	if (!ctx) return c;
	ctx.fillStyle = '#000';
	ctx.fillStyle = c; // browser normalises to #rrggbb or rgba(...)
	const norm = ctx.fillStyle as string;
	if (norm.startsWith('#')) {
		const r = parseInt(norm.slice(1, 3), 16);
		const g = parseInt(norm.slice(3, 5), 16);
		const b = parseInt(norm.slice(5, 7), 16);
		return `rgba(${r},${g},${b},${alpha})`;
	}
	return norm;
}

/** Theme-derived colours for axes / gridlines / tooltips. */
export function chartTheme() {
	return {
		grid: withAlpha(cssVar('--border') || '#888', 0.6),
		tick: cssVar('--text-faint') || '#888',
		tooltipBg: cssVar('--bg-elev') || '#fff',
		tooltipText: cssVar('--text') || '#000',
		tooltipBorder: cssVar('--border') || '#ccc'
	};
}

export { Chart };
