<script lang="ts">
	// Fleet world map: nodes plotted at their physical location (from the node-id
	// region prefix), interconnected by their real internal WireGuard mesh (edges
	// from each node's iBGP peers, which run over that WG mesh). Edges are drawn as
	// arcs; hovering a node highlights its links and the rest dims. Land outline =
	// Natural Earth 110m (static asset), equirectangular projection.
	import { onMount } from 'svelte';
	import type { NodeHealthValue, FleetLink } from '$lib/types';
	import { t } from '$lib/i18n.svelte';

	let {
		nodes,
		links = [],
		onview
	}: {
		nodes: { node_id: string; health: NodeHealthValue }[];
		// Physical WG mesh edges (from the fleet-overview endpoint), already deduped.
		links?: FleetLink[];
		// Reports the node ids currently inside the selected view window, so callers
		// (e.g. the dashboard table) can filter to the same region.
		onview?: (ids: string[]) => void;
	} = $props();

	const COLOR: Record<NodeHealthValue, string> = {
		ok: 'var(--c-ok)',
		stale: 'var(--c-warn)',
		degraded: 'var(--c-bad)',
		down: 'var(--c-down)',
		unknown: 'var(--c-unknown)'
	};
	const ORDER: NodeHealthValue[] = ['ok', 'stale', 'degraded', 'down', 'unknown'];

	// Region prefix → [lat, lon, city]. Covers the current fleet; unknowns fall to 0,0.
	const GEO: Record<string, [number, number, string]> = {
		hkg: [22.32, 114.17, 'Hong Kong'],
		can: [23.13, 113.26, 'Guangzhou'],
		pvg: [31.23, 121.47, 'Shanghai'],
		sha: [31.23, 121.47, 'Shanghai'],
		tpe: [25.03, 121.57, 'Taipei'],
		tyo: [35.68, 139.77, 'Tokyo'],
		nrt: [35.77, 140.39, 'Tokyo'],
		lax: [34.05, -118.24, 'Los Angeles'],
		sjc: [37.34, -121.89, 'San Jose'],
		fra: [50.11, 8.68, 'Frankfurt'],
		ams: [52.37, 4.9, 'Amsterdam'],
		sin: [1.35, 103.82, 'Singapore'],
		lon: [51.51, -0.13, 'London']
	};

	const W = 1000;
	const H = 500;
	// View window: 'region' crops to Asia-Pacific → North America (where the fleet
	// lives, nodes get room); 'global' shows the whole projection.
	type ViewKey = 'south' | 'east' | 'asia' | 'global';
	let view = $state<ViewKey>('asia');
	// Wide (panoramic) crops so the map fills the card width while staying short.
	const VIEWS: Record<ViewKey, { x: number; y: number; w: number; h: number }> = {
		south: { x: 317, y: 172, w: 83, h: 28 }, // 华南: HK / Guangzhou / Taipei
		east: { x: 328, y: 156, w: 100, h: 33 }, // 华东: Shanghai / Taipei
		asia: { x: 301, y: 139, w: 183, h: 61 }, // 东亚: all East-Asia nodes
		global: { x: 0, y: 42, w: W, h: 360 } // 全球: poles cropped for a wider frame
	};
	const VIEW_OPTS: { k: ViewKey; label: string }[] = [
		{ k: 'south', label: 'topo.south' },
		{ k: 'east', label: 'topo.east' },
		{ k: 'asia', label: 'topo.asia' },
		{ k: 'global', label: 'topo.global' }
	];
	let vb = $derived(VIEWS[view]);
	// Pacific-centred equirectangular: the fleet straddles the Pacific, so centre the
	// map there (LON0) — trans-Pacific links then cross the middle like real cables.
	const LON0 = 165;
	const relLon = (lon: number) => ((lon - LON0 + 540) % 360) - 180;
	const proj = (lon: number, lat: number): [number, number] => [
		((relLon(lon) + 180) / 360) * W,
		((90 - lat) / 180) * H
	];
	// Inverse (pixel → lon/lat) so fanned-out node pixels get a synthetic lon/lat and
	// great-circle edges land exactly on the dots.
	const invLon = (x: number) => (x / W) * 360 - 180 + LON0;
	const invLat = (y: number) => 90 - (y / H) * 180;
	const region = (id: string) => (id.match(/^[a-z]+/i)?.[0] ?? id).toLowerCase();

	// Place nodes; fan out members that share one region so they don't overlap.
	let placed = $derived.by(() => {
		const byRegion = new Map<string, typeof nodes>();
		for (const n of nodes) {
			const r = region(n.node_id);
			if (!byRegion.has(r)) byRegion.set(r, []);
			byRegion.get(r)!.push(n);
		}
		const out: {
			node_id: string;
			health: NodeHealthValue;
			x: number;
			y: number;
			lon: number;
			lat: number;
			city: string;
		}[] = [];
		for (const [r, group] of byRegion) {
			const geo = GEO[r] ?? [0, 0, r.toUpperCase()];
			const [bx, by] = proj(geo[1], geo[0]);
			group.forEach((n, i) => {
				let x = bx;
				let y = by;
				if (group.length > 1) {
					// Spread co-located nodes (same city) by a constant on-screen amount
					// (~7px) regardless of zoom, so they stay a tight pair on the city.
					const a = (-90 + (i * 360) / group.length) * (Math.PI / 180);
					const fan = vb.w * 0.0045;
					x = bx + fan * Math.cos(a);
					y = by + fan * Math.sin(a);
				}
				out.push({ ...n, x, y, lon: invLon(x), lat: invLat(y), city: geo[2] });
			});
		}
		return out;
	});
	let posOf = $derived(new Map(placed.map((p) => [p.node_id, p])));

	// Node ids whose marker falls inside the current view window → drives the
	// dashboard table's region filter.
	let visibleIds = $derived(
		placed
			.filter((p) => p.x >= vb.x && p.x <= vb.x + vb.w && p.y >= vb.y && p.y <= vb.y + vb.h)
			.map((p) => p.node_id)
	);
	$effect(() => {
		onview?.(visibleIds);
	});

	// Great-circle path between two lon/lat points (the route real submarine cables
	// follow): interpolate on the sphere and project each sample, lifting the pen at
	// the map seam. Trans-Pacific links bow north through the North Pacific.
	const D2R = Math.PI / 180;
	const R2D = 180 / Math.PI;
	function gcPath(lon1: number, lat1: number, lon2: number, lat2: number): string {
		const v1 = [
			Math.cos(lat1 * D2R) * Math.cos(lon1 * D2R),
			Math.cos(lat1 * D2R) * Math.sin(lon1 * D2R),
			Math.sin(lat1 * D2R)
		];
		const v2 = [
			Math.cos(lat2 * D2R) * Math.cos(lon2 * D2R),
			Math.cos(lat2 * D2R) * Math.sin(lon2 * D2R),
			Math.sin(lat2 * D2R)
		];
		const dot = Math.max(-1, Math.min(1, v1[0] * v2[0] + v1[1] * v2[1] + v1[2] * v2[2]));
		const w = Math.acos(dot);
		const N = 64;
		let d = '';
		let prevX: number | null = null;
		for (let i = 0; i <= N; i++) {
			const f = i / N;
			const s1 = w < 1e-6 ? 1 - f : Math.sin((1 - f) * w) / Math.sin(w);
			const s2 = w < 1e-6 ? f : Math.sin(f * w) / Math.sin(w);
			const x = s1 * v1[0] + s2 * v2[0];
			const y = s1 * v1[1] + s2 * v2[1];
			const z = s1 * v1[2] + s2 * v2[2];
			const lat = Math.atan2(z, Math.hypot(x, y)) * R2D;
			const lon = Math.atan2(y, x) * R2D;
			const [px, py] = proj(lon, lat);
			if (prevX !== null && Math.abs(px - prevX) > W / 2) d += ` M${px.toFixed(1)} ${py.toFixed(1)}`;
			else d += `${prevX === null ? 'M' : 'L'}${px.toFixed(1)} ${py.toFixed(1)}`;
			prevX = px;
		}
		return d;
	}

	// --- land outline ---
	let landPaths = $state<string[]>([]);
	// Cut a ring at the map seam: where a segment jumps across the map, run it out to
	// the near edge, close that piece, and resume from the far edge. Without this the
	// auto-close of a seam-spanning polygon draws a line straight across the map.
	function ringToPath(ring: number[][]): string {
		const pts: [number, number][] = ring.map(([lon, lat]) => proj(lon, lat));
		// Rotate the ring to begin at the first seam crossing, so every cut piece runs
		// edge→edge and closes along the border (no line straight across the map).
		let start = 0;
		for (let i = 1; i < pts.length; i++) {
			if (Math.abs(pts[i][0] - pts[i - 1][0]) > W / 2) {
				start = i;
				break;
			}
		}
		const rot = start ? pts.slice(start).concat(pts.slice(0, start)) : pts;

		const subpaths: [number, number][][] = [];
		let cur: [number, number][] = [];
		let prev: [number, number] | null = null;
		for (const p of rot) {
			if (prev && Math.abs(p[0] - prev[0]) > W / 2) {
				const yEdge = (prev[1] + p[1]) / 2;
				cur.push([prev[0] > W / 2 ? W : 0, yEdge]);
				subpaths.push(cur);
				cur = [[p[0] > W / 2 ? W : 0, yEdge]];
			}
			cur.push(p);
			prev = p;
		}
		if (cur.length) subpaths.push(cur);
		return subpaths
			.filter((s) => s.length > 1)
			.map((s) => 'M' + s.map((p) => `${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join('L') + 'Z')
			.join(' ');
	}
	async function loadLand() {
		try {
			const gj = await (await fetch('/world-land.geojson')).json();
			const paths: string[] = [];
			for (const f of gj.features ?? []) {
				const g = f.geometry;
				if (!g) continue;
				// Skip Antarctica (spans every longitude → seam artifacts, no nodes there).
				const bb = f.bbox as number[] | undefined;
				if (bb && bb[3] < -55) continue;
				if (g.type === 'Polygon') {
					for (const ring of g.coordinates) paths.push(ringToPath(ring));
				} else if (g.type === 'MultiPolygon') {
					for (const poly of g.coordinates) for (const ring of poly) paths.push(ringToPath(ring));
				}
			}
			landPaths = paths;
		} catch {
			landPaths = [];
		}
	}

	// --- real physical WG links from each node's OSPF adjacencies ---
	// iBGP is a logical full-mesh (every node peers with every other over the routed
	// mesh), so it does NOT reflect the physical topology. The actual point-to-point
	// WireGuard tunnels are the OSPF neighbours (each over a `wg-<peer>` interface).
	type Edge = {
		a: string;
		b: string;
		live: boolean;
		ifaceA?: string | null;
		ifaceB?: string | null;
		cost?: number | null;
	};
	// Edges come straight from the fleet-overview links (server already deduped them).
	let edges = $derived<Edge[]>(
		links.map((l) => ({ a: l.a, b: l.b, ifaceA: l.a_iface, ifaceB: l.b_iface, cost: l.cost, live: true }))
	);

	onMount(loadLand);

	// --- interaction state ---
	let filter = $state<NodeHealthValue | null>(null);
	let hovered = $state<string | null>(null);
	let neighbors = $derived.by(() => {
		const s = new Set<string>();
		if (hovered) {
			s.add(hovered);
			for (const e of edges) {
				if (e.a === hovered) s.add(e.b);
				else if (e.b === hovered) s.add(e.a);
			}
		}
		return s;
	});

	// Only render connections that are actually live (carrying routes); drop the
	// configured-but-idle ones so the map shows real links only.
	let liveEdges = $derived(edges.filter((e) => e.live));

	// Hovered link → tooltip (wg interfaces + OSPF cost) at the link midpoint.
	const edgeKey = (e: Edge) => `${e.a}|${e.b}`;
	let hoverEdge = $state<string | null>(null);
	let hoveredEdge = $derived.by(() => {
		const e = hoverEdge ? edges.find((x) => edgeKey(x) === hoverEdge) : null;
		if (!e) return null;
		const pa = posOf.get(e.a);
		const pb = posOf.get(e.b);
		if (!pa || !pb) return null;
		return { e, mx: (pa.x + pb.x) / 2, my: (pa.y + pb.y) / 2 };
	});

	let counts = $derived.by(() => {
		const c = {} as Record<NodeHealthValue, number>;
		for (const h of ORDER) c[h] = 0;
		for (const n of nodes) c[n.health] = (c[n.health] ?? 0) + 1;
		return c;
	});
	function toggle(h: NodeHealthValue) {
		filter = filter === h ? null : h;
	}
	function nodeDim(n: { node_id: string; health: NodeHealthValue }): boolean {
		if (filter !== null && n.health !== filter) return true;
		if (hovered && !neighbors.has(n.node_id)) return true;
		return false;
	}
	function edgeState(e: { a: string; b: string; live: boolean }) {
		const touches = hovered && (e.a === hovered || e.b === hovered);
		if (hovered && !touches) return { op: 0.06, hot: false };
		if (filter !== null) return { op: 0.18, hot: false };
		return { op: 0.6, hot: !!touches };
	}
</script>

<div class="topo">
	<div class="chips">
		<div class="chips-left">
			{#each ORDER as h (h)}
				{#if counts[h] > 0}
					<button class="chip" class:active={filter === h} onclick={() => toggle(h)}>
						<span class="sw" style="background:{COLOR[h]}"></span>
						{t(`health.${h}`)}
						<span class="chip-count">{counts[h]}</span>
					</button>
				{/if}
			{/each}
		</div>
		<div class="seg">
			{#each VIEW_OPTS as o (o.k)}
				<button class:on={view === o.k} onclick={() => (view = o.k)}>{t(o.label)}</button>
			{/each}
		</div>
	</div>

	<div class="stage" style="aspect-ratio:{vb.w} / {vb.h}">
		<svg viewBox="{vb.x} {vb.y} {vb.w} {vb.h}" preserveAspectRatio="xMidYMid meet">
			<!-- one merged path so overlapping rings don't stack opacity into patches -->
			<g class="land">
				<path d={landPaths.join(' ')} />
			</g>
			<g class="edges" fill="none">
				{#each liveEdges as e (e.a + e.b)}
					{@const pa = posOf.get(e.a)}
					{@const pb = posOf.get(e.b)}
					{#if pa && pb}
						{@const st = edgeState(e)}
						{@const d = gcPath(pa.lon, pa.lat, pb.lon, pb.lat)}
						{@const on = hoverEdge === edgeKey(e)}
						<path
							{d}
							stroke="var(--accent)"
							stroke-width={on || st.hot ? 2.4 : 1.3}
							opacity={hoverEdge && !on ? 0.12 : st.op}
							stroke-linecap="round"
							vector-effect="non-scaling-stroke"
						/>
						<!-- wide invisible hit area for hover -->
						<path
							{d}
							stroke="transparent"
							stroke-width="10"
							stroke-linecap="round"
							vector-effect="non-scaling-stroke"
							style="cursor:pointer"
							role="presentation"
							onpointerenter={() => (hoverEdge = edgeKey(e))}
							onpointerleave={() => (hoverEdge = null)}
						/>
					{/if}
				{/each}
			</g>
		</svg>

		{#each placed as n (n.node_id)}
			<a
				href="/nodes/{n.node_id}"
				class="node"
				class:dim={nodeDim(n)}
				class:hot={hovered === n.node_id}
				style="left:{((n.x - vb.x) / vb.w) * 100}%; top:{((n.y - vb.y) / vb.h) * 100}%"
				onpointerenter={() => (hovered = n.node_id)}
				onpointerleave={() => (hovered = null)}
			>
				<span class="halo" style="background:{COLOR[n.health]}"></span>
				<span class="dot" style="background:{COLOR[n.health]}"></span>
				<span class="tip">
					<span class="tip-id mono">{n.node_id}</span>
					<span class="tip-meta">{n.city} · {t(`health.${n.health}`)}</span>
				</span>
			</a>
		{/each}

		{#if hoveredEdge}
			<div
				class="edge-tip"
				style="left:{((hoveredEdge.mx - vb.x) / vb.w) * 100}%; top:{((hoveredEdge.my - vb.y) / vb.h) * 100}%"
			>
				<span class="et-row"><span class="mono">{hoveredEdge.e.a}</span> {hoveredEdge.e.ifaceA ?? '—'}</span>
				<span class="et-row"><span class="mono">{hoveredEdge.e.b}</span> {hoveredEdge.e.ifaceB ?? '—'}</span>
				{#if hoveredEdge.e.cost != null}<span class="et-cost">OSPF cost {hoveredEdge.e.cost}</span>{/if}
			</div>
		{/if}
	</div>
</div>

<style>
	.topo {
		display: flex;
		flex-direction: column;
		gap: 0.85rem;
	}
	.chips {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 0.6rem;
	}
	.chips-left {
		display: flex;
		flex-wrap: wrap;
		gap: 0.4rem;
	}
	/* segmented region/global toggle */
	.seg {
		display: inline-flex;
		flex: none;
		border: 1px solid var(--border-strong);
		border-radius: 999px;
		overflow: hidden;
	}
	.seg button {
		border: none;
		background: transparent;
		color: var(--text-dim);
		font: inherit;
		font-size: 0.78rem;
		font-weight: 550;
		padding: 0.28rem 0.7rem;
		cursor: pointer;
		transition: background 0.12s, color 0.12s;
	}
	.seg button:hover {
		color: var(--text);
	}
	.seg button.on {
		background: var(--accent);
		color: #fff;
	}
	.sw {
		width: 9px;
		height: 9px;
		border-radius: 3px;
		flex: none;
	}
	.stage {
		position: relative;
		/* Full card width; the wide view aspect-ratio (set inline) keeps it short. */
		width: 100%;
		background: var(--bg);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		overflow: hidden;
	}
	.stage svg {
		display: block;
		width: 100%;
		height: 100%;
	}
	.land path {
		/* border-strong reads clearly against the --bg ocean in both light and dark. */
		fill: var(--border-strong);
		stroke: none;
		opacity: 0.7;
	}
	.node {
		position: absolute;
		width: 0;
		height: 0;
		transition: opacity 0.15s;
	}
	.node.dim {
		opacity: 0.3;
	}
	.node .dot {
		position: absolute;
		left: 0;
		top: 0;
		width: 9px;
		height: 9px;
		border-radius: 50%;
		transform: translate(-50%, -50%);
		border: 1.5px solid var(--bg-elev);
		box-shadow: var(--shadow);
		transition: width 0.12s, height 0.12s;
	}
	.node.hot .dot {
		width: 13px;
		height: 13px;
	}
	.node .halo {
		position: absolute;
		left: 0;
		top: 0;
		width: 9px;
		height: 9px;
		border-radius: 50%;
		transform: translate(-50%, -50%);
		opacity: 0.25;
		transition: width 0.2s, height 0.2s, opacity 0.2s;
	}
	.node.hot .halo {
		width: 30px;
		height: 30px;
		opacity: 0.18;
	}
	.node .tip {
		position: absolute;
		left: 0;
		top: 0;
		transform: translate(-50%, calc(-100% - 12px));
		display: none;
		flex-direction: column;
		gap: 1px;
		padding: 0.3rem 0.55rem;
		background: var(--text);
		color: var(--bg-elev);
		border-radius: var(--radius-sm);
		white-space: nowrap;
		box-shadow: var(--shadow-lg);
		pointer-events: none;
		z-index: 2;
	}
	.node.hot .tip {
		display: flex;
	}
	.tip-id {
		font-size: 0.78rem;
		font-weight: 700;
	}
	.tip-meta {
		font-size: 0.68rem;
		opacity: 0.8;
	}
	.edge-tip {
		position: absolute;
		transform: translate(-50%, -50%);
		display: flex;
		flex-direction: column;
		gap: 1px;
		padding: 0.3rem 0.55rem;
		background: var(--text);
		color: var(--bg-elev);
		border-radius: var(--radius-sm);
		white-space: nowrap;
		box-shadow: var(--shadow-lg);
		pointer-events: none;
		z-index: 3;
		font-size: 0.72rem;
	}
	.edge-tip .et-row .mono {
		font-weight: 700;
	}
	.edge-tip .et-cost {
		opacity: 0.7;
		font-size: 0.66rem;
	}
</style>
