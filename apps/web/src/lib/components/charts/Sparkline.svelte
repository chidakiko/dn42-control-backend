<script module lang="ts">
	let uid = 0;
</script>

<script lang="ts">
	// Sparkline / area trend: smooth curve, vertical gradient area fill, and an
	// optional hover readout (guide line + dot + value tooltip). Backwards compatible
	// with the old polyline props; `smooth` and `interactive` are opt-out / opt-in.
	let {
		values,
		width = 120,
		height = 34,
		color = 'var(--c-accent)',
		fill = true,
		strokeWidth = 1.5,
		smooth = true,
		interactive = false,
		format
	}: {
		values: number[];
		width?: number;
		height?: number;
		color?: string;
		fill?: boolean;
		strokeWidth?: number;
		smooth?: boolean;
		interactive?: boolean;
		format?: (v: number) => string;
	} = $props();

	const gid = `spark-${++uid}`;
	const pad = 2;

	let geom = $derived.by(() => {
		const n = values?.length ?? 0;
		if (n === 0) return { line: '', area: '', pts: [] as { x: number; y: number }[] };
		const min = Math.min(...values);
		const max = Math.max(...values);
		const span = max - min || 1;
		const x = (i: number) => pad + (i * (width - 2 * pad)) / Math.max(1, n - 1);
		const y = (v: number) => height - pad - ((v - min) / span) * (height - 2 * pad);
		const pts = values.map((v, i) => ({ x: x(i), y: y(v) }));

		let line: string;
		if (smooth && n > 2) {
			// Catmull-Rom → cubic bezier for a smooth curve.
			line = `M${pts[0].x.toFixed(1)} ${pts[0].y.toFixed(1)}`;
			for (let i = 0; i < n - 1; i++) {
				const p0 = pts[i - 1] ?? pts[i];
				const p1 = pts[i];
				const p2 = pts[i + 1];
				const p3 = pts[i + 2] ?? p2;
				const c1x = p1.x + (p2.x - p0.x) / 6;
				const c1y = p1.y + (p2.y - p0.y) / 6;
				const c2x = p2.x - (p3.x - p1.x) / 6;
				const c2y = p2.y - (p3.y - p1.y) / 6;
				line += ` C${c1x.toFixed(1)} ${c1y.toFixed(1)} ${c2x.toFixed(1)} ${c2y.toFixed(1)} ${p2.x.toFixed(1)} ${p2.y.toFixed(1)}`;
			}
		} else {
			line = pts.map((p, i) => `${i ? 'L' : 'M'}${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(' ');
		}
		const area = `${line} L${pts[n - 1].x.toFixed(1)} ${height} L${pts[0].x.toFixed(1)} ${height} Z`;
		return { line, area, pts };
	});

	let hover = $state<number | null>(null);

	function onMove(e: PointerEvent) {
		const n = values?.length ?? 0;
		if (!interactive || n === 0) return;
		const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
		const ratio = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
		hover = Math.round(ratio * (n - 1));
	}
	function fmt(v: number): string {
		return format ? format(v) : String(v);
	}
</script>

<div
	class="spark-wrap"
	style="width:{width}px; height:{height}px"
	onpointermove={onMove}
	onpointerleave={() => (hover = null)}
	role="img"
	aria-label="trend"
>
	<svg viewBox="0 0 {width} {height}" {width} {height} preserveAspectRatio="none">
		<defs>
			<linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
				<stop offset="0" stop-color={color} stop-opacity="0.28" />
				<stop offset="1" stop-color={color} stop-opacity="0" />
			</linearGradient>
		</defs>
		{#if fill && geom.area}
			<path d={geom.area} fill="url(#{gid})" />
		{/if}
		<path
			d={geom.line}
			fill="none"
			stroke={color}
			stroke-width={strokeWidth}
			stroke-linejoin="round"
			stroke-linecap="round"
			vector-effect="non-scaling-stroke"
		/>
		{#if interactive && hover != null && geom.pts[hover]}
			<line
				x1={geom.pts[hover].x}
				y1="0"
				x2={geom.pts[hover].x}
				y2={height}
				stroke={color}
				stroke-width="1"
				opacity="0.35"
				vector-effect="non-scaling-stroke"
			/>
			<circle cx={geom.pts[hover].x} cy={geom.pts[hover].y} r="2.6" fill={color} vector-effect="non-scaling-stroke" />
		{/if}
	</svg>
	{#if interactive && hover != null && values[hover] != null}
		<span
			class="spark-tip"
			style="left:{(geom.pts[hover].x / width) * 100}%"
		>{fmt(values[hover])}</span>
	{/if}
</div>

<style>
	.spark-wrap {
		position: relative;
		display: block;
	}
	.spark-wrap svg {
		display: block;
	}
	.spark-tip {
		position: absolute;
		top: -0.2rem;
		transform: translate(-50%, -100%);
		background: var(--text);
		color: var(--bg-elev);
		font-size: 0.7rem;
		font-variant-numeric: tabular-nums;
		font-weight: 600;
		padding: 0.1rem 0.4rem;
		border-radius: var(--radius-sm);
		white-space: nowrap;
		pointer-events: none;
		box-shadow: var(--shadow);
	}
</style>
