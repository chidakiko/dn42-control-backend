<script lang="ts">
	// Combined fleet map + node list. Left (60% on wide screens) is a pan/zoom world
	// map; right (40%) is a compact, scrollable node list synced with the map. Nodes in
	// the same region are clustered into one larger marker (with a "+N" badge); zooming
	// past a threshold splits a cluster into its individual nodes.
	import { onMount } from 'svelte';
	import { api, ApiError, errorMessage } from '$lib/api';
	import { toast } from '$lib/toast.svelte';
	import { t } from '$lib/i18n.svelte';
	import { relTime } from '$lib/format';
	import type { NodeHealthValue, FleetLink, FleetOverviewNode } from '$lib/types';
	import Icon, { type IconName } from '$lib/components/Icon.svelte';
	import HealthBadge from '$lib/components/HealthBadge.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';

	let {
		nodes,
		links = []
	}: { nodes: FleetOverviewNode[]; links?: FleetLink[] } = $props();

	const COLOR: Record<NodeHealthValue, string> = {
		ok: 'var(--c-ok)',
		stale: 'var(--c-warn)',
		degraded: 'var(--c-bad)',
		down: 'var(--c-down)',
		unknown: 'var(--c-unknown)'
	};
	const ORDER: NodeHealthValue[] = ['ok', 'stale', 'degraded', 'down', 'unknown'];
	// Worst-health ranking for a cluster's combined dot colour (higher = worse).
	const SEV: Record<NodeHealthValue, number> = { down: 4, degraded: 3, stale: 2, unknown: 1, ok: 0 };

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
	// Pan/zoom view. The view HEIGHT (world units) is fixed per zoom level; the view
	// WIDTH adapts to the stage's pixel aspect ratio, so the map always fills its box
	// without distortion and the HTML node overlays stay pixel-aligned. This lets the
	// layout drive the map's proportions instead of a hard-coded aspect ratio.
	const BASE_VH = 230;
	const CX0 = 535;
	const CY0 = 162;
	const MAX_K = 16;
	// Zoom at/above which same-region clusters split into individual fanned nodes.
	const SPLIT_K = 4;

	let k = $state(1);
	let cx = $state(CX0);
	let cy = $state(CY0);
	let stageW = $state(0);
	let stageH = $state(0);

	let aspect = $derived(stageH > 0 ? stageW / stageH : 1.7);
	let vh = $derived(BASE_VH / k);
	let vw = $derived(vh * aspect);
	let vx = $derived(cx - vw / 2);
	let vy = $derived(cy - vh / 2);
	let expanded = $derived(k >= SPLIT_K);

	const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));
	function clampCenter() {
		cx = vw >= W ? W / 2 : clamp(cx, vw / 2, W - vw / 2);
		cy = vh >= H ? H / 2 : clamp(cy, vh / 2, H - vh / 2);
	}

	// Pacific-centred equirectangular: the fleet straddles the Pacific, so centre there.
	const LON0 = 165;
	const relLon = (lon: number) => ((lon - LON0 + 540) % 360) - 180;
	const proj = (lon: number, lat: number): [number, number] => [
		((relLon(lon) + 180) / 360) * W,
		((90 - lat) / 180) * H
	];
	const invLon = (x: number) => (x / W) * 360 - 180 + LON0;
	const invLat = (y: number) => 90 - (y / H) * 180;
	const region = (id: string) => (id.match(/^[a-z]+/i)?.[0] ?? id).toLowerCase();

	// Group nodes by region; one cluster per region at its city coordinates.
	let clusters = $derived.by(() => {
		const m = new Map<string, FleetOverviewNode[]>();
		for (const n of nodes) {
			const r = region(n.node_id);
			if (!m.has(r)) m.set(r, []);
			m.get(r)!.push(n);
		}
		return [...m].map(([r, group]) => {
			const geo = GEO[r] ?? [0, 0, r.toUpperCase()];
			const [x, y] = proj(geo[1], geo[0]);
			const worst = group.reduce<NodeHealthValue>(
				(w, n) => (SEV[n.health] > SEV[w] ? n.health : w),
				'ok'
			);
			return { region: r, city: geo[2], group, x, y, worst };
		});
	});

	// Effective on-map position per node: cluster centre when collapsed; fanned out
	// around the city when expanded so co-located nodes separate. Fan radius scales
	// with the view so the on-screen spread stays roughly constant.
	let posOf = $derived.by(() => {
		const m = new Map<string, { x: number; y: number; lon: number; lat: number }>();
		for (const c of clusters) {
			if (expanded && c.group.length > 1) {
				c.group.forEach((n, i) => {
					const a = (-90 + (i * 360) / c.group.length) * (Math.PI / 180);
					// keep co-located nodes a tight cluster once split (just enough to not overlap)
					const fan = vw * 0.011;
					const x = c.x + fan * Math.cos(a);
					const y = c.y + fan * Math.sin(a);
					m.set(n.node_id, { x, y, lon: invLon(x), lat: invLat(y) });
				});
			} else {
				for (const n of c.group) m.set(n.node_id, { x: c.x, y: c.y, lon: invLon(c.x), lat: invLat(c.y) });
			}
		}
		return m;
	});

	// pixel position (% of current viewBox) helpers for HTML overlays
	const leftPct = (x: number) => ((x - vx) / vw) * 100;
	const topPct = (y: number) => ((y - vy) / vh) * 100;

	// --- great-circle edges ---
	const D2R = Math.PI / 180;
	const R2D = 180 / Math.PI;
	function gcPath(lon1: number, lat1: number, lon2: number, lat2: number): string {
		const v1 = [Math.cos(lat1 * D2R) * Math.cos(lon1 * D2R), Math.cos(lat1 * D2R) * Math.sin(lon1 * D2R), Math.sin(lat1 * D2R)];
		const v2 = [Math.cos(lat2 * D2R) * Math.cos(lon2 * D2R), Math.cos(lat2 * D2R) * Math.sin(lon2 * D2R), Math.sin(lat2 * D2R)];
		const dot = Math.max(-1, Math.min(1, v1[0] * v2[0] + v1[1] * v2[1] + v1[2] * v2[2]));
		const w = Math.acos(dot);
		const N = 48;
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

	type Edge = { a: string; b: string; ifaceA?: string | null; ifaceB?: string | null; cost?: number | null };
	let edges = $derived<Edge[]>(links.map((l) => ({ a: l.a, b: l.b, ifaceA: l.a_iface, ifaceB: l.b_iface, cost: l.cost })));
	// Drop intra-cluster edges (both endpoints collapsed onto the same point).
	let drawnEdges = $derived(
		edges.filter((e) => {
			const pa = posOf.get(e.a);
			const pb = posOf.get(e.b);
			return pa && pb && (pa.x !== pb.x || pa.y !== pb.y);
		})
	);

	// --- land outline ---
	let landPaths = $state<string[]>([]);
	function ringToPath(ring: number[][]): string {
		const pts: [number, number][] = ring.map(([lon, lat]) => proj(lon, lat));
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
				const bb = f.bbox as number[] | undefined;
				if (bb && bb[3] < -55) continue;
				if (g.type === 'Polygon') for (const ring of g.coordinates) paths.push(ringToPath(ring));
				else if (g.type === 'MultiPolygon') for (const poly of g.coordinates) for (const ring of poly) paths.push(ringToPath(ring));
			}
			landPaths = paths;
		} catch {
			landPaths = [];
		}
	}
	onMount(loadLand);

	// --- interaction: pan / zoom ---
	let stageEl = $state<HTMLDivElement | null>(null);
	let dragging = $state(false);
	let moved = false;
	let lastX = 0;
	let lastY = 0;
	// transient "hold Ctrl to zoom" hint shown when the user scrolls without a modifier
	let wheelHint = $state(false);
	let wheelHintT: ReturnType<typeof setTimeout> | null = null;

	function onwheel(e: WheelEvent) {
		// Don't hijack the page scroll: only zoom when Ctrl/⌘ is held (also matches the
		// trackpad pinch gesture, which arrives as a ctrlKey wheel event). Otherwise let
		// the page scroll normally and briefly hint how to zoom.
		if (!(e.ctrlKey || e.metaKey)) {
			wheelHint = true;
			if (wheelHintT) clearTimeout(wheelHintT);
			wheelHintT = setTimeout(() => (wheelHint = false), 1300);
			return;
		}
		e.preventDefault();
		if (!stageEl) return;
		const rect = stageEl.getBoundingClientRect();
		const fx = (e.clientX - rect.left) / rect.width;
		const fy = (e.clientY - rect.top) / rect.height;
		const wx = vx + fx * vw;
		const wy = vy + fy * vh;
		const nk = clamp(k * (e.deltaY < 0 ? 1.2 : 1 / 1.2), 1, MAX_K);
		if (nk === k) return;
		const nvh = BASE_VH / nk;
		const nvw = nvh * aspect;
		k = nk;
		cx = wx - fx * nvw + nvw / 2;
		cy = wy - fy * nvh + nvh / 2;
		clampCenter();
	}
	function onpointerdown(e: PointerEvent) {
		dragging = true;
		moved = false;
		lastX = e.clientX;
		lastY = e.clientY;
		stageEl?.setPointerCapture?.(e.pointerId);
	}
	function onpointermove(e: PointerEvent) {
		if (!stageEl) return;
		const rect = stageEl.getBoundingClientRect();
		// track cursor (stage-relative px) so the edge tooltip can follow it
		ptrX = e.clientX - rect.left;
		ptrY = e.clientY - rect.top;
		if (!dragging) return;
		const dx = ((e.clientX - lastX) / rect.width) * vw;
		const dy = ((e.clientY - lastY) / rect.height) * vh;
		if (Math.abs(e.clientX - lastX) + Math.abs(e.clientY - lastY) > 2) moved = true;
		cx -= dx;
		cy -= dy;
		clampCenter();
		lastX = e.clientX;
		lastY = e.clientY;
	}
	function onpointerup() {
		dragging = false;
	}
	function zoomBy(factor: number) {
		k = clamp(k * factor, 1, MAX_K);
		clampCenter();
	}
	function reset() {
		k = 1;
		cx = CX0;
		cy = CY0;
	}
	// Click a cluster → zoom in and centre on it (so it splits into its members).
	function focusCluster(c: { x: number; y: number }) {
		cx = c.x;
		cy = c.y;
		k = Math.max(SPLIT_K, k);
		clampCenter();
	}

	// --- edge hover tooltip (follows the cursor + clamped into view, so it stays
	// visible even when the link's midpoint is panned/zoomed off-screen) ---
	let hoverEdge = $state<string | null>(null);
	let ptrX = $state(0);
	let ptrY = $state(0);
	const edgeKey = (e: Edge) => `${e.a}|${e.b}`;
	let hoveredEdge = $derived(hoverEdge ? edges.find((x) => edgeKey(x) === hoverEdge) ?? null : null);
	let tipLeft = $derived(clamp(ptrX + 14, 4, Math.max(4, stageW - 170)));
	let tipTop = $derived(clamp(ptrY + 14, 4, Math.max(4, stageH - 60)));

	// --- shared hover state (map ↔ list) ---
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
	let filter = $state<NodeHealthValue | null>(null);
	let counts = $derived.by(() => {
		const c = {} as Record<NodeHealthValue, number>;
		for (const h of ORDER) c[h] = 0;
		for (const n of nodes) c[n.health] = (c[n.health] ?? 0) + 1;
		return c;
	});
	function toggle(h: NodeHealthValue) {
		filter = filter === h ? null : h;
	}
	function nodeDim(n: FleetOverviewNode): boolean {
		if (filter !== null && n.health !== filter) return true;
		if (hovered && !neighbors.has(n.node_id)) return true;
		return false;
	}
	function clusterDim(c: { group: FleetOverviewNode[] }): boolean {
		// a cluster is lit if any member passes the current filter/hover.
		return c.group.every((n) => nodeDim(n));
	}

	// List grouped by region (mirrors the map clusters): worst-health first, then city;
	// nodes within a group also worst-first. Multi-node groups get a collapsible header.
	let listGroups = $derived.by(() =>
		clusters
			.map((c) => ({
				...c,
				nodes: [...c.group].sort(
					(a, b) => SEV[b.health] - SEV[a.health] || a.node_id.localeCompare(b.node_id)
				)
			}))
			.sort((a, b) => SEV[b.worst] - SEV[a.worst] || a.city.localeCompare(b.city))
	);
	let collapsed = $state(new Set<string>());
	function toggleGroup(r: string) {
		const s = new Set(collapsed);
		if (s.has(r)) s.delete(r);
		else s.add(r);
		collapsed = s;
	}
	let linkCount = $derived(edges.length);
	function cityOf(id: string): string {
		return (GEO[region(id)] ?? [0, 0, region(id).toUpperCase()])[2];
	}

	const CAP_META: Record<string, { icon: IconName; label: string }> = {
		dns: { icon: 'dns', label: 'DNS' },
		'rpki-cache': { icon: 'shield-check', label: 'RPKI' },
		'bird-router': { icon: 'bird', label: 'BIRD' },
		'wg-gateway': { icon: 'wireguard', label: 'WG' }
	};
	function caps(n: FleetOverviewNode): { icon: IconName; label: string }[] {
		return ['dns', 'rpki-cache', 'bird-router', 'wg-gateway']
			.filter((c) => (n.capabilities ?? []).includes(c))
			.map((c) => CAP_META[c]);
	}
	// per-node physical WG-link degree, shown as a compact "links" count in the list.
	let degree = $derived.by(() => {
		const m = new Map<string, number>();
		for (const e of edges) {
			m.set(e.a, (m.get(e.a) ?? 0) + 1);
			m.set(e.b, (m.get(e.b) ?? 0) + 1);
		}
		return m;
	});
	// freshness of the last snapshot → colours the "last seen" stamp.
	function freshness(iso: string | null): string {
		if (!iso) return 'old';
		const age = (Date.now() - new Date(iso).getTime()) / 1000;
		if (age < 150) return 'fresh';
		if (age < 900) return 'aging';
		return 'old';
	}

	async function requestSnapshot(e: Event, id: string) {
		e.preventDefault();
		e.stopPropagation();
		try {
			const r = await api.notifyNode(id, 'snapshot_request', 'dashboard refresh');
			toast.success(t('dash.snapshotRequested', r.delivered));
		} catch (err) {
			if (err instanceof ApiError && err.status === 409) toast.error(t('disc.refused'));
			else toast.error(errorMessage(err));
		}
	}
</script>

<div class="fleetmap">
	<!-- fleet summary bar -->
	<div class="summary">
		<div class="sum-stats">
			<span class="sum-item"><strong>{nodes.length}</strong> {t('topo.sumNodes')}</span>
			<span class="sum-sep">·</span>
			<span class="sum-item"><strong>{clusters.length}</strong> {t('topo.sumRegions')}</span>
			<span class="sum-sep">·</span>
			<span class="sum-item"><strong>{linkCount}</strong> {t('topo.sumLinks')}</span>
		</div>
		<span class="r-grow"></span>
		<div class="sum-health">
			{#each ORDER as h (h)}
				{#if counts[h] > 0}
					<button
						class="sum-pill"
						class:active={filter === h}
						onclick={() => toggle(h)}
						title={t(`health.${h}`)}
					>
						<span class="sw" style="background:{COLOR[h]}"></span>{counts[h]}
					</button>
				{/if}
			{/each}
		</div>
	</div>

	<div class="fleet">
		<!-- ===== map ===== -->
		<div class="map">
			<div
			class="stage"
			class:grabbing={dragging}
			bind:this={stageEl}
			bind:clientWidth={stageW}
			bind:clientHeight={stageH}
			role="application"
			aria-label="fleet map"
			{onwheel}
			{onpointerdown}
			{onpointermove}
			onpointerup={onpointerup}
			onpointerleave={onpointerup}
		>
			<svg viewBox="{vx} {vy} {vw} {vh}" preserveAspectRatio="xMidYMid meet">
				<g class="land"><path d={landPaths.join(' ')} /></g>
				<g class="edges" fill="none">
					{#each drawnEdges as e (e.a + e.b)}
						{@const pa = posOf.get(e.a)}
						{@const pb = posOf.get(e.b)}
						{#if pa && pb}
							{@const d = gcPath(pa.lon, pa.lat, pb.lon, pb.lat)}
							{@const isHot = hoverEdge === edgeKey(e)}
							{@const lit = !hovered || hovered === e.a || hovered === e.b}
							<path
								{d}
								stroke="var(--accent)"
								stroke-width={isHot ? 2.4 : lit && hovered ? 2.2 : 1.3}
								opacity={hoverEdge ? (isHot ? 0.9 : 0.1) : lit ? 0.6 : 0.1}
								stroke-linecap="round"
								vector-effect="non-scaling-stroke"
							/>
							<!-- wide invisible hit area for hover; doesn't block panning -->
							<path
								{d}
								stroke="transparent"
								stroke-width="12"
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

			<!-- markers: collapsed clusters, or individual nodes when zoomed in -->
			{#if !expanded}
				{#each clusters as c (c.region)}
					{#if c.group.length > 1}
						<button
							class="cluster"
							class:dim={clusterDim(c)}
							style="left:{leftPct(c.x)}%; top:{topPct(c.y)}%"
							onclick={() => focusCluster(c)}
							onpointerdown={(e) => e.stopPropagation()}
							onpointerenter={() => (hovered = c.group[0].node_id)}
							onpointerleave={() => (hovered = null)}
							title="{c.city} · {c.group.length}"
						>
							<span class="c-dot" style="background:{COLOR[c.worst]}">{c.group.length}</span>
							<span class="c-label">{c.city} <span class="c-plus">+{c.group.length - 1}</span></span>
						</button>
					{:else}
						{@const n = c.group[0]}
						<a
							href="/nodes/{n.node_id}"
							class="node"
							class:dim={nodeDim(n)}
							class:hot={hovered === n.node_id}
							style="left:{leftPct(c.x)}%; top:{topPct(c.y)}%"
							onpointerdown={(e) => e.stopPropagation()}
							onpointerenter={() => (hovered = n.node_id)}
							onpointerleave={() => (hovered = null)}
						>
							<span class="halo" style="background:{COLOR[n.health]}"></span>
							<span class="dot" style="background:{COLOR[n.health]}"></span>
							<span class="m-label">{n.node_id}</span>
						</a>
					{/if}
				{/each}
			{:else}
				{#each nodes as n (n.node_id)}
					{@const p = posOf.get(n.node_id)}
					{#if p}
						<a
							href="/nodes/{n.node_id}"
							class="node"
							class:dim={nodeDim(n)}
							class:hot={hovered === n.node_id}
							style="left:{leftPct(p.x)}%; top:{topPct(p.y)}%"
							onpointerdown={(e) => e.stopPropagation()}
							onpointerenter={() => (hovered = n.node_id)}
							onpointerleave={() => (hovered = null)}
						>
							<span class="halo" style="background:{COLOR[n.health]}"></span>
							<span class="dot" style="background:{COLOR[n.health]}"></span>
							<span class="m-label">{n.node_id}</span>
						</a>
					{/if}
				{/each}
			{/if}

			<!-- zoom controls — stop pointerdown so the stage's pan/pointer-capture doesn't
			     swallow the button's click -->
			<div class="zoom">
				<button
					onpointerdown={(e) => e.stopPropagation()}
					onclick={() => zoomBy(1.4)}
					aria-label="zoom in"
					title="放大">+</button
				>
				<button
					onpointerdown={(e) => e.stopPropagation()}
					onclick={() => zoomBy(1 / 1.4)}
					aria-label="zoom out"
					title="缩小">−</button
				>
				<button
					onpointerdown={(e) => e.stopPropagation()}
					onclick={reset}
					aria-label="reset"
					title="重置"><Icon name="refresh" size={13} /></button
				>
			</div>
			{#if !expanded && clusters.some((c) => c.group.length > 1)}
				<div class="hint">{t('topo.zoomHint')}</div>
			{/if}

			{#if wheelHint}
				<div class="wheel-hint">{t('topo.ctrlZoom')}</div>
			{/if}

			{#if hoveredEdge}
				<div class="edge-tip" style="left:{tipLeft}px; top:{tipTop}px">
					<span class="et-row"><span class="mono">{hoveredEdge.a}</span> {hoveredEdge.ifaceA ?? '—'}</span>
					<span class="et-row"><span class="mono">{hoveredEdge.b}</span> {hoveredEdge.ifaceB ?? '—'}</span>
					{#if hoveredEdge.cost != null}<span class="et-cost">OSPF cost {hoveredEdge.cost}</span>{/if}
				</div>
			{/if}
		</div>
	</div>

	<!-- ===== node list (grouped by region) ===== -->
		<div class="list">
			{#each listGroups as g (g.region)}
				{#if g.nodes.length > 1}
					<div class="grp">
						<div class="grp-head">
							<Tooltip label={t('topo.focusTip')} side="top">
								{#snippet trigger(props)}
									<button
										{...props}
										class="grp-focus"
										onclick={() => focusCluster(g)}
										onpointerenter={() => (hovered = g.nodes[0].node_id)}
										onpointerleave={() => (hovered = null)}
									>
										<span class="grp-dot" style="background:{COLOR[g.worst]}"></span>
										<span class="grp-city">{g.city}</span>
										<span class="grp-count">{g.nodes.length}</span>
									</button>
								{/snippet}
							</Tooltip>
							<span class="r-grow"></span>
							<button class="grp-toggle" onclick={() => toggleGroup(g.region)} aria-label="toggle">
								<span class="chev" class:closed={collapsed.has(g.region)}>
									<Icon name="chevron-down" size={14} />
								</span>
							</button>
						</div>
						{#if !collapsed.has(g.region)}
							<div class="grp-body">
								{#each g.nodes as n (n.node_id)}{@render nodeRow(n)}{/each}
							</div>
						{/if}
					</div>
				{:else}
					{@render nodeRow(g.nodes[0])}
				{/if}
			{/each}
		</div>
	</div>
</div>

{#snippet nodeRow(n: FleetOverviewNode)}
	{@const mism =
		n.observed_generation != null &&
		n.desired_generation != null &&
		n.observed_generation !== n.desired_generation}
	{@const deg = degree.get(n.node_id) ?? 0}
	<a
		href="/nodes/{n.node_id}"
		class="row"
		class:dim={nodeDim(n)}
		class:hot={hovered === n.node_id}
		style="--row-accent:{COLOR[n.health]}"
		onpointerenter={() => (hovered = n.node_id)}
		onpointerleave={() => (hovered = null)}
	>
		<div class="r-main">
			<div class="r-head">
				<span class="r-id mono">{n.node_id}</span>
				<span class="r-city">{cityOf(n.node_id)}</span>
				{#each caps(n) as c (c.label)}
					<span class="cap-chip"><Icon name={c.icon} size={10} />{c.label}</span>
				{/each}
				<span class="r-grow"></span>
				<HealthBadge value={n.health} compact />
			</div>

			<div class="r-meta">
				<Tooltip label={t('topo.genTip')} side="top">
					{#snippet trigger(props)}
						<span {...props} class="m-item"
							>gen {n.desired_generation ?? '—'}{#if mism}<span class="m-stale"
									>→{n.observed_generation}</span
								>{/if}</span
						>
					{/snippet}
				</Tooltip>
				{#if deg > 0}
					<Tooltip label={t('topo.linkTip')} side="top">
						{#snippet trigger(props)}
							<span {...props} class="m-item"><Icon name="route" size={11} />{deg}</span>
						{/snippet}
					</Tooltip>
				{/if}
				{#if n.drift_count > 0}
					<Tooltip label={t('topo.driftTip')} side="top">
						{#snippet trigger(props)}
							<span {...props} class="chip-drift">{n.drift_count} drift</span>
						{/snippet}
					</Tooltip>
				{/if}
				<span class="r-grow"></span>
				<span class="m-when {freshness(n.last_snapshot_at)}">{relTime(n.last_snapshot_at)}</span>
			</div>
		</div>
		<Tooltip label={t('dash.requestSnapshot')} side="left">
			{#snippet trigger(props)}
				<button
					{...props}
					class="r-action"
					onclick={(e) => requestSnapshot(e, n.node_id)}
					aria-label={t('dash.requestSnapshot')}
				>
					<Icon name="refresh" size={13} />
				</button>
			{/snippet}
		</Tooltip>
	</a>
{/snippet}

<style>
	.fleetmap {
		display: flex;
		flex-direction: column;
		gap: 0.9rem;
	}
	/* fleet summary bar */
	.summary {
		display: flex;
		align-items: center;
		gap: 0.6rem;
		flex-wrap: wrap;
		padding: 0.5rem 0.7rem;
		background: var(--bg-elev);
		border: 1px solid var(--border);
		border-radius: var(--radius);
	}
	.sum-stats {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		font-size: 0.82rem;
		color: var(--text-dim);
	}
	.sum-item strong {
		font-size: 1rem;
		color: var(--text);
		font-variant-numeric: tabular-nums;
		margin-right: 0.15rem;
	}
	.sum-sep {
		color: var(--text-faint);
	}
	.sum-health {
		display: flex;
		gap: 0.35rem;
	}
	.sum-pill {
		display: inline-flex;
		align-items: center;
		gap: 0.3rem;
		border: 1px solid var(--border);
		background: var(--bg);
		border-radius: 999px;
		padding: 0.12rem 0.55rem;
		font-size: 0.76rem;
		font-variant-numeric: tabular-nums;
		color: var(--text-dim);
		cursor: pointer;
	}
	.sum-pill.active {
		border-color: var(--accent);
		color: var(--text);
	}
	.fleet {
		display: flex;
		flex-direction: column;
		gap: 1rem;
	}
	.map {
		display: flex;
		flex-direction: column;
		gap: 0.7rem;
		min-width: 0;
		min-height: 0;
	}
	.list {
		display: flex;
		flex-direction: column;
		gap: 4px;
		overflow-y: auto;
		max-height: 360px;
	}
	@media (min-width: 900px) {
		/* one balanced row: the layout sets the height, the map fills it (its viewBox
		   adapts to the box aspect), and the list scrolls within the same height. */
		.fleet {
			flex-direction: row;
			align-items: stretch;
			height: clamp(300px, 40vh, 440px);
		}
		.map {
			flex: 0 0 60%;
		}
		.list {
			flex: 0 0 calc(40% - 1rem);
			max-height: none;
		}
	}

	/* region group */
	.grp {
		display: flex;
		flex-direction: column;
	}
	.grp-head {
		display: flex;
		align-items: center;
		gap: 0.2rem;
		padding: 0.15rem 0.2rem;
	}
	.grp-focus {
		display: inline-flex;
		align-items: center;
		gap: 0.4rem;
		background: none;
		border: none;
		padding: 0.2rem 0.3rem;
		border-radius: var(--radius-sm);
		cursor: pointer;
		color: var(--text);
	}
	.grp-focus:hover {
		background: var(--bg-elev-2);
	}
	.grp-dot {
		width: 9px;
		height: 9px;
		border-radius: 50%;
		flex: none;
	}
	.grp-city {
		font-size: 0.78rem;
		font-weight: 700;
	}
	.grp-count {
		font-size: 0.68rem;
		font-weight: 700;
		color: var(--text-faint);
		background: var(--bg-elev-2);
		border-radius: 999px;
		padding: 0 0.4rem;
		font-variant-numeric: tabular-nums;
	}
	.grp-toggle {
		background: none;
		border: none;
		color: var(--text-faint);
		cursor: pointer;
		display: inline-flex;
		padding: 0.2rem;
	}
	.grp-toggle:hover {
		color: var(--text);
	}
	.chev {
		display: inline-flex;
		transition: transform 0.15s;
	}
	.chev.closed {
		transform: rotate(-90deg);
	}
	/* grouped rows sit indented under their region header */
	.grp-body {
		display: flex;
		flex-direction: column;
		gap: 2px;
		padding-left: 0.65rem;
		margin-left: 0.3rem;
		border-left: 1px solid var(--border);
	}
	.sw {
		width: 9px;
		height: 9px;
		border-radius: 3px;
		flex: none;
	}

	.stage {
		position: relative;
		flex: 1 1 auto;
		min-height: 300px;
		background: var(--bg);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		overflow: hidden;
		cursor: grab;
		touch-action: none;
	}
	.stage.grabbing {
		cursor: grabbing;
	}
	.stage svg {
		display: block;
		width: 100%;
		height: 100%;
	}
	.land path {
		fill: var(--border-strong);
		stroke: none;
		opacity: 0.7;
	}

	.node {
		position: absolute;
		width: 0;
		height: 0;
	}
	.node.dim {
		opacity: 0.3;
	}
	.node .dot {
		position: absolute;
		left: 0;
		top: 0;
		width: 10px;
		height: 10px;
		border-radius: 50%;
		transform: translate(-50%, -50%);
		border: 1.5px solid var(--bg-elev);
		box-shadow: var(--shadow);
		transition: width 0.12s, height 0.12s;
	}
	.node.hot .dot {
		width: 14px;
		height: 14px;
	}
	.node .halo {
		position: absolute;
		left: 0;
		top: 0;
		width: 10px;
		height: 10px;
		border-radius: 50%;
		transform: translate(-50%, -50%);
		opacity: 0.22;
	}
	.node.hot .halo {
		width: 28px;
		height: 28px;
	}
	.m-label {
		position: absolute;
		left: 0;
		top: 0;
		transform: translate(-50%, -190%);
		font-size: 0.66rem;
		font-weight: 600;
		color: var(--text);
		background: color-mix(in srgb, var(--bg-elev) 80%, transparent);
		padding: 0 0.25rem;
		border-radius: 3px;
		white-space: nowrap;
		opacity: 0;
		transition: opacity 0.12s;
	}
	.node.hot .m-label {
		opacity: 1;
	}

	/* clustered (collapsed) marker: zero-size anchor at the city, so the DOT sits
	   exactly on the city coordinate and the label floats to its right (the dot, not
	   the dot+label box, marks the region). */
	.cluster {
		position: absolute;
		width: 0;
		height: 0;
		background: none;
		border: none;
		padding: 0;
		cursor: pointer;
	}
	.cluster.dim {
		opacity: 0.3;
	}
	.c-dot {
		position: absolute;
		left: 0;
		top: 0;
		transform: translate(-50%, -50%);
		box-sizing: border-box;
		width: 22px;
		height: 22px;
		border-radius: 50%;
		display: flex;
		align-items: center;
		justify-content: center;
		font-size: 0.72rem;
		font-weight: 700;
		color: #fff;
		border: 2px solid var(--bg-elev);
		box-shadow: var(--shadow);
	}
	.c-label {
		position: absolute;
		left: 0;
		top: 0;
		transform: translate(16px, -50%);
		font-size: 0.68rem;
		font-weight: 600;
		color: var(--text);
		background: color-mix(in srgb, var(--bg-elev) 80%, transparent);
		padding: 0 0.3rem;
		border-radius: 3px;
		white-space: nowrap;
		pointer-events: none;
	}
	.c-plus {
		color: var(--accent);
	}

	.zoom {
		position: absolute;
		right: 0.5rem;
		bottom: 0.5rem;
		display: flex;
		flex-direction: column;
		gap: 2px;
	}
	.zoom button {
		width: 26px;
		height: 26px;
		display: flex;
		align-items: center;
		justify-content: center;
		background: var(--bg-elev);
		border: 1px solid var(--border-strong);
		color: var(--text-dim);
		font-size: 1rem;
		line-height: 1;
		cursor: pointer;
	}
	.zoom button:first-child {
		border-radius: var(--radius-sm) var(--radius-sm) 0 0;
	}
	.zoom button:last-child {
		border-radius: 0 0 var(--radius-sm) var(--radius-sm);
	}
	.zoom button:hover {
		color: var(--text);
		background: var(--bg-elev-2);
	}
	.hint {
		position: absolute;
		left: 0.5rem;
		bottom: 0.5rem;
		font-size: 0.68rem;
		color: var(--text-faint);
		background: color-mix(in srgb, var(--bg-elev) 70%, transparent);
		padding: 0.1rem 0.4rem;
		border-radius: 3px;
		pointer-events: none;
	}
	/* centred "hold Ctrl to zoom" hint while the user scrolls the page over the map */
	.wheel-hint {
		position: absolute;
		inset: 0;
		display: flex;
		align-items: center;
		justify-content: center;
		font-size: 0.92rem;
		font-weight: 600;
		color: #fff;
		background: rgba(0, 0, 0, 0.4);
		pointer-events: none;
		animation: wh-fade 0.15s ease;
	}
	@keyframes wh-fade {
		from {
			opacity: 0;
		}
		to {
			opacity: 1;
		}
	}
	/* edge tooltip follows the cursor (clamped into the stage) so it's always visible */
	.edge-tip {
		position: absolute;
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
		z-index: 5;
		font-size: 0.72rem;
	}
	.edge-tip .et-row .mono {
		font-weight: 700;
	}
	.edge-tip .et-cost {
		opacity: 0.7;
		font-size: 0.66rem;
	}

	/* ===== list rows ===== */
	.row {
		position: relative;
		display: flex;
		align-items: center;
		gap: 0.5rem;
		padding: 0.4rem 0.55rem 0.4rem 0.8rem;
		border-radius: var(--radius-sm);
		border: 1px solid transparent;
		color: inherit;
	}
	/* health-coloured accent rail (CF-style) */
	.row::before {
		content: '';
		position: absolute;
		left: 0;
		top: 0.4rem;
		bottom: 0.4rem;
		width: 3px;
		border-radius: 0 3px 3px 0;
		background: var(--row-accent);
	}
	.row:hover,
	.row.hot {
		background: var(--bg-elev-2);
		border-color: var(--border);
		text-decoration: none;
	}
	.row.dim {
		opacity: 0.4;
	}
	.r-main {
		flex: 1;
		min-width: 0;
		display: flex;
		flex-direction: column;
		gap: 0.22rem;
	}
	.r-head {
		display: flex;
		align-items: center;
		gap: 0.35rem;
		min-width: 0;
	}
	.r-id {
		font-size: 0.88rem;
		font-weight: 600;
		color: var(--text);
	}
	.r-city {
		font-size: 0.72rem;
		color: var(--text-faint);
	}
	.r-grow {
		flex: 1;
	}
	.cap-chip {
		display: inline-flex;
		align-items: center;
		gap: 0.15rem;
		font-size: 0.6rem;
		font-weight: 600;
		color: var(--text-dim);
		background: var(--bg-elev-2);
		border: 1px solid var(--border);
		border-radius: 4px;
		padding: 0.02rem 0.26rem;
		flex: none;
	}
	.r-meta {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		font-size: 0.72rem;
		color: var(--text-faint);
	}
	.m-item {
		display: inline-flex;
		align-items: center;
		gap: 0.2rem;
		font-variant-numeric: tabular-nums;
	}
	.m-stale {
		color: var(--c-warn);
		font-weight: 600;
		margin-left: 0.1rem;
	}
	.chip-drift {
		font-size: 0.66rem;
		font-weight: 700;
		color: var(--c-bad);
		background: color-mix(in srgb, var(--c-bad) 14%, transparent);
		border-radius: 4px;
		padding: 0.05rem 0.32rem;
		font-variant-numeric: tabular-nums;
	}
	.m-when {
		font-variant-numeric: tabular-nums;
	}
	.m-when.fresh {
		color: var(--c-ok);
	}
	.m-when.aging {
		color: var(--c-warn);
	}
	.r-action {
		flex: none;
		align-self: center;
		width: 26px;
		height: 26px;
		display: flex;
		align-items: center;
		justify-content: center;
		background: transparent;
		border: 1px solid transparent;
		border-radius: var(--radius-sm);
		color: var(--text-faint);
		cursor: pointer;
		opacity: 0;
	}
	.row:hover .r-action {
		opacity: 1;
	}
	.r-action:hover {
		color: var(--text);
		border-color: var(--border);
		background: var(--bg-elev);
	}
</style>
