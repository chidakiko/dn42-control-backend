<script lang="ts">
	// Radar-style responsive trend chart: smoothed multi-series lines over a
	// timestamped x-axis, faint horizontal/vertical gridlines, numeric y ticks,
	// date/time x ticks, and an interactive hover crosshair + tooltip card.
	// Modelled on Cloudflare Radar's "traffic trend" chart.
	import { parseTs } from '$lib/format';
	import { locale } from '$lib/i18n.svelte';

	interface Series {
		label: string;
		color: string;
		values: number[];
		/** draw a gradient area under the line (primary series) */
		fill?: boolean;
		/** render as a dashed comparison line (e.g. previous period) */
		dash?: boolean;
	}

	let {
		series,
		timestamps,
		height = 200,
		zeroBased = false,
		format = (v: number) => v.toLocaleString()
	}: {
		series: Series[];
		timestamps: (string | null)[];
		height?: number;
		/** anchor the y-axis at 0 (for additive metrics like traffic) instead of
		 *  zooming tightly to the data band */
		zeroBased?: boolean;
		format?: (v: number) => string;
	} = $props();

	// Measured width of the wrapper (responsive).
	let w = $state(0);

	const padL = 58;
	const padR = 14;
	const padT = 10;
	const padB = 24;

	let n = $derived(Math.max(0, ...series.map((s) => s.values.length)));
	let innerW = $derived(Math.max(0, w - padL - padR));
	let innerH = $derived(Math.max(0, height - padT - padB));

	// y-domain zoomed to the data band (route counts rarely touch 0, so a 0
	// baseline would flatten the trend); pad 12% on each side.
	let domain = $derived.by(() => {
		const all = series.flatMap((s) => s.values);
		if (!all.length) return { lo: 0, hi: 1 };
		const mn = Math.min(...all);
		const mx = Math.max(...all);
		if (zeroBased) return { lo: 0, hi: mx === 0 ? 1 : mx * 1.08 };
		if (mn === mx) return { lo: mn - 1, hi: mx + 1 };
		// tight zoom to the data band (Radar "min-y" mode) so small variations
		// in an otherwise-stable series stay visible instead of flattening out.
		const pad = (mx - mn) * 0.08;
		return { lo: mn - pad, hi: mx + pad };
	});

	function xAt(i: number): number {
		if (n <= 1) return padL + innerW / 2;
		return padL + (innerW * i) / (n - 1);
	}
	function yAt(v: number): number {
		const { lo, hi } = domain;
		const t = hi === lo ? 0.5 : (v - lo) / (hi - lo);
		return padT + innerH * (1 - t);
	}

	// Catmull-Rom → cubic-bézier smoothing (matches Sparkline).
	function smoothPath(pts: { x: number; y: number }[]): string {
		if (pts.length < 2) return pts.length ? `M ${pts[0].x} ${pts[0].y}` : '';
		let d = `M ${pts[0].x} ${pts[0].y}`;
		for (let i = 0; i < pts.length - 1; i++) {
			const p0 = pts[i - 1] ?? pts[i];
			const p1 = pts[i];
			const p2 = pts[i + 1];
			const p3 = pts[i + 2] ?? p2;
			const c1x = p1.x + (p2.x - p0.x) / 6;
			const c1y = p1.y + (p2.y - p0.y) / 6;
			const c2x = p2.x - (p3.x - p1.x) / 6;
			const c2y = p2.y - (p3.y - p1.y) / 6;
			d += ` C ${c1x} ${c1y}, ${c2x} ${c2y}, ${p2.x} ${p2.y}`;
		}
		return d;
	}

	let plots = $derived(
		w > 0
			? series.map((s) => {
					const pts = s.values.map((v, i) => ({ x: xAt(i), y: yAt(v) }));
					const line = smoothPath(pts);
					const area =
						s.fill && pts.length > 1
							? `${line} L ${pts[pts.length - 1].x} ${padT + innerH} L ${pts[0].x} ${padT + innerH} Z`
							: '';
					return { ...s, line, area };
				})
			: []
	);

	// Horizontal gridlines + y labels: lo, mid, hi.
	let yTicks = $derived.by(() => {
		const { lo, hi } = domain;
		return [hi, (hi + lo) / 2, lo].map((v) => ({ v, y: yAt(v) }));
	});

	// x ticks: up to ~6 evenly spaced indices, labelled date or time depending
	// on the total span.
	let spanMs = $derived.by(() => {
		const a = parseTs(timestamps[0]);
		const b = parseTs(timestamps[timestamps.length - 1]);
		return a && b ? Math.abs(b.getTime() - a.getTime()) : 0;
	});
	let useDate = $derived(spanMs > 36 * 3600 * 1000);

	function fmtTick(ts: string | null): string {
		const d = parseTs(ts);
		if (!d) return '';
		return useDate
			? d.toLocaleDateString(locale.tag, { month: 'short', day: 'numeric' })
			: d.toLocaleTimeString(locale.tag, { hour: '2-digit', minute: '2-digit' });
	}

	let xTicks = $derived.by(() => {
		if (n < 2 || w === 0) return [];
		const count = Math.min(6, n);
		const out: { x: number; label: string }[] = [];
		for (let k = 0; k < count; k++) {
			const i = Math.round((k * (n - 1)) / (count - 1));
			out.push({ x: xAt(i), label: fmtTick(timestamps[i]) });
		}
		return out;
	});

	function fmtFull(ts: string | null): string {
		const d = parseTs(ts);
		if (!d) return '';
		return d.toLocaleString(locale.tag, {
			month: 'short',
			day: 'numeric',
			hour: '2-digit',
			minute: '2-digit'
		});
	}

	// --- hover ---
	let hoverIdx = $state<number | null>(null);

	function onMove(e: PointerEvent) {
		if (n < 1) return;
		const rect = (e.currentTarget as SVGRectElement).getBoundingClientRect();
		const mx = e.clientX - rect.left;
		const t = innerW <= 0 ? 0 : (mx - padL) / innerW;
		hoverIdx = Math.max(0, Math.min(n - 1, Math.round(t * (n - 1))));
	}
	function onLeave() {
		hoverIdx = null;
	}

	let hoverX = $derived(hoverIdx === null ? 0 : xAt(hoverIdx));
	// flip the tooltip to the left of the crosshair when near the right edge
	let tipLeft = $derived(hoverX > padL + innerW * 0.62);
	const uid = $props.id();

	// draw dashed comparison lines behind solid ones; list solids first in the
	// tooltip so the current period reads before the "previous" overlay.
	let drawPlots = $derived([...plots].sort((a, b) => (a.dash ? 0 : 1) - (b.dash ? 0 : 1)));
	let tipSeries = $derived([...series].sort((a, b) => (a.dash ? 1 : 0) - (b.dash ? 1 : 0)));
</script>

<div class="trend" style="height:{height}px" bind:clientWidth={w}>
	{#if w > 0 && n > 0}
		<svg width={w} {height} role="img">
			<defs>
				{#each plots as p, i (p.label + i)}
					{#if p.fill}
						<linearGradient id="tg-{uid}-{i}" x1="0" y1="0" x2="0" y2="1">
							<stop offset="0%" stop-color={p.color} stop-opacity="0.22" />
							<stop offset="100%" stop-color={p.color} stop-opacity="0" />
						</linearGradient>
					{/if}
				{/each}
			</defs>

			<!-- horizontal gridlines + y labels -->
			{#each yTicks as tk (tk.v)}
				<line class="grid h" x1={padL} y1={tk.y} x2={w - padR} y2={tk.y} />
				<text class="ylab" x={padL - 8} y={tk.y} text-anchor="end" dominant-baseline="middle"
					>{format(tk.v)}</text
				>
			{/each}

			<!-- vertical gridlines + x labels -->
			{#each xTicks as tk (tk.x)}
				<line class="grid v" x1={tk.x} y1={padT} x2={tk.x} y2={padT + innerH} />
				<text class="xlab" x={tk.x} y={height - 8} text-anchor="middle">{tk.label}</text>
			{/each}

			<!-- series (dashed comparison lines behind solid ones) -->
			{#each drawPlots as p, i (p.label + i)}
				{#if p.area}<path d={p.area} fill="url(#tg-{uid}-{i})" />{/if}
				<path
					d={p.line}
					fill="none"
					stroke={p.color}
					stroke-width={p.dash ? 1.5 : 2}
					stroke-linejoin="round"
					stroke-linecap="round"
					stroke-dasharray={p.dash ? '5 4' : 'none'}
					opacity={p.dash ? 0.55 : 1}
				/>
			{/each}

			<!-- hover crosshair + dots (current period only) -->
			{#if hoverIdx !== null}
				<line class="cross" x1={hoverX} y1={padT} x2={hoverX} y2={padT + innerH} />
				{#each plots as p, i (p.label + i)}
					{#if !p.dash && p.values[hoverIdx] !== undefined}
						<circle cx={hoverX} cy={yAt(p.values[hoverIdx])} r="3.5" fill="var(--bg-elev)" stroke={p.color} stroke-width="2" />
					{/if}
				{/each}
			{/if}

			<!-- pointer capture overlay -->
			<rect
				role="presentation"
				x={padL}
				y={padT}
				width={Math.max(0, innerW)}
				height={Math.max(0, innerH)}
				fill="transparent"
				onpointermove={onMove}
				onpointerleave={onLeave}
			/>
		</svg>

		{#if hoverIdx !== null}
			<div class="tip" class:left={tipLeft} style="left:{hoverX}px">
				<div class="tip-h">{fmtFull(timestamps[hoverIdx])}</div>
				{#each tipSeries as s (s.label)}
					{#if s.values[hoverIdx] !== undefined}
						<div class="tip-r" class:dim={s.dash}>
							<span class="dot" class:dash={s.dash} style="--c:{s.color}"></span>
							<span class="tl">{s.label}</span>
							<span class="tv">{format(s.values[hoverIdx])}</span>
						</div>
					{/if}
				{/each}
			</div>
		{/if}
	{/if}
</div>

<style>
	.trend {
		position: relative;
		width: 100%;
	}
	svg {
		display: block;
	}
	.grid {
		stroke: var(--border);
	}
	.grid.h {
		stroke-dasharray: 2 4;
		opacity: 0.7;
	}
	.grid.v {
		stroke-dasharray: 2 4;
		opacity: 0.5;
	}
	.ylab,
	.xlab {
		fill: var(--text-faint);
		font-size: 11px;
		font-variant-numeric: tabular-nums;
	}
	.cross {
		stroke: var(--text-faint);
		stroke-width: 1;
		opacity: 0.6;
	}
	.tip {
		position: absolute;
		top: 6px;
		transform: translateX(0.6rem);
		pointer-events: none;
		background: var(--bg-elev);
		border: 1px solid var(--border);
		border-radius: var(--radius-sm);
		box-shadow: 0 6px 18px rgb(0 0 0 / 0.18);
		padding: 0.45rem 0.6rem;
		font-size: 0.76rem;
		white-space: nowrap;
		z-index: 2;
	}
	.tip.left {
		transform: translateX(calc(-100% - 0.6rem));
	}
	.tip-h {
		font-weight: 600;
		margin-bottom: 0.3rem;
		color: var(--text);
	}
	.tip-r {
		display: flex;
		align-items: center;
		gap: 0.4rem;
		line-height: 1.5;
	}
	.tip-r.dim {
		opacity: 0.7;
	}
	.dot {
		width: 8px;
		height: 8px;
		border-radius: 50%;
		flex: none;
		background: var(--c);
	}
	/* dashed comparison series: hollow ring instead of a filled dot */
	.dot.dash {
		background: transparent;
		border: 1.5px dashed var(--c);
	}
	.tl {
		color: var(--text-dim);
	}
	.tv {
		margin-left: auto;
		font-weight: 600;
		font-variant-numeric: tabular-nums;
		color: var(--text);
	}
</style>
