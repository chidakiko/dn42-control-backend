<script lang="ts">
	import { onMount, untrack } from 'svelte';
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { api, ApiError, errorMessage } from '$lib/api';
	import { toast } from '$lib/toast.svelte';
	import { fmtTime } from '$lib/format';
	import { t } from '$lib/i18n.svelte';
	import { autoRefresh } from '$lib/refresh.svelte';
	import type { NodeOut, PeeringOut, NodeHealthValue } from '$lib/types';
	import HealthBadge from '$lib/components/HealthBadge.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import Modal from '$lib/components/Modal.svelte';
	import JsonEditor from '$lib/components/JsonEditor.svelte';
	import JsonView from '$lib/components/JsonView.svelte';
	import PeeringsTab from '$lib/components/node/PeeringsTab.svelte';
	import PeerWizard from '$lib/components/node/PeerWizard.svelte';
	import SpecResourceTab, { type SpecField } from '$lib/components/node/SpecResourceTab.svelte';
	import GenerationsTab from '$lib/components/node/GenerationsTab.svelte';
	import StatusEventsTab from '$lib/components/node/StatusEventsTab.svelte';
	import RoutingTab from '$lib/components/node/RoutingTab.svelte';
	import NodeDnsTab from '$lib/components/node/NodeDnsTab.svelte';
	import InternalTopologyTab from '$lib/components/node/InternalTopologyTab.svelte';
	import AgentTokensTab from '$lib/components/node/AgentTokensTab.svelte';
	import RouteTuningTab from '$lib/components/node/RouteTuningTab.svelte';
	import NodeTrends from '$lib/components/node/NodeTrends.svelte';
	import NodeSelfMetrics from '$lib/components/node/NodeSelfMetrics.svelte';

	let nodeId = $derived(page.params.id ?? '');

	let node = $state<NodeOut | null>(null);
	let loading = $state(true);
	let error = $state('');
	let peerings = $state<PeeringOut[]>([]);
	let health = $state<NodeHealthValue | null>(null);
	let showWizard = $state(false);

	// "Disconnected" = the control center hasn't heard from the node recently.
	// stale = warning, down = error. Real-time push is unavailable in both.
	let disconnected = $derived(health === 'stale' || health === 'down');
	let severity = $derived<'down' | 'stale' | null>(
		health === 'down' ? 'down' : health === 'stale' ? 'stale' : null
	);

	// Two-level navigation: 5 top-level groups, each with sub-tabs. The content
	// selector (`tab`) stays flat (the 12 content ids); the group is derived from
	// it. Leaf groups (overview / dns) have no sub-tabs — the group id *is* the
	// content id.
	interface SubTab {
		id: string;
		key: string;
	}
	interface Group {
		id: string;
		key: string;
		tabs: SubTab[];
	}
	const GROUPS: Group[] = [
		{ id: 'overview', key: 'node.tab.overview', tabs: [] },
		{
			id: 'connectivity',
			key: 'node.grp.connectivity',
			tabs: [
				{ id: 'peerings', key: 'node.tab.peerings' },
				{ id: 'interfaces', key: 'node.tab.interfaces' },
				{ id: 'bgp', key: 'node.tab.bgp' },
				{ id: 'internal', key: 'node.tab.internal' }
			]
		},
		{
			id: 'routing-grp',
			key: 'node.grp.routing',
			tabs: [
				{ id: 'routing', key: 'node.tab.routing' },
				{ id: 'tuning', key: 'node.tab.tuning' }
			]
		},
		{ id: 'dns', key: 'node.tab.dns', tabs: [] },
		{
			id: 'ops',
			key: 'node.grp.ops',
			tabs: [
				{ id: 'generations', key: 'node.tab.generations' },
				{ id: 'status', key: 'node.tab.status' },
				{ id: 'desired', key: 'node.tab.desired' },
				{ id: 'tokens', key: 'node.tab.tokens' }
			]
		}
	];
	// All valid content ids (leaf group id, or each sub-tab id).
	const ALL_TAB_IDS = GROUPS.flatMap((g) => (g.tabs.length ? g.tabs.map((s) => s.id) : [g.id]));
	// Default content id when a top-level group is clicked: its first sub-tab, or
	// the group itself for leaf groups.
	const groupContentId = (g: Group): string => (g.tabs.length ? g.tabs[0].id : g.id);
	const groupOf = (tabId: string): Group =>
		GROUPS.find((g) => g.id === tabId || g.tabs.some((s) => s.id === tabId)) ?? GROUPS[0];

	// Deep-link support: honour ?tab=<id> on first load (e.g. from the dashboard
	// routing card), falling back to overview for unknown values.
	const initialTab = page.url.searchParams.get('tab');
	let tab = $state(initialTab && ALL_TAB_IDS.includes(initialTab) ? initialTab : 'overview');
	let activeGroup = $derived(groupOf(tab));

	let desired = $state<unknown>(null);
	let desiredErr = $state('');

	// edit modal
	let showEdit = $state(false);
	let saving = $state(false);
	let e = $state({ asn: '', router_id: '', site: '', loopback_ipv4: '', loopback_ipv6: '', link_local: '' });
	let baseText = $state('{}');
	let labelsText = $state('{}');
	let baseEditor: JsonEditor;
	let labelsEditor: JsonEditor;

	async function loadNode() {
		// Spinner only on the first load; background polls update silently so they
		// never unmount the active tab / open form.
		if (!node) loading = true;
		error = '';
		try {
			node = await api.getNode(nodeId);
		} catch (err) {
			error = errorMessage(err);
		} finally {
			loading = false;
		}
	}

	async function loadPeerings() {
		try {
			peerings = await api.listPeerings(nodeId);
		} catch {
			/* surfaced inside the peerings tab */
		}
	}

	async function loadHealth() {
		try {
			const h = await api.nodeHealth(nodeId);
			health = h.health;
		} catch {
			// 404 = never reported → leave as unknown (not "disconnected")
			health = null;
		}
	}

	onMount(loadPeerings);

	// Initial load + keep node/health fresh on each auto-refresh tick so the
	// disconnection banner appears/clears live.
	$effect(() => {
		autoRefresh.tick;
		nodeId; // reload when navigating to a different node
		untrack(() => {
			loadNode();
			loadHealth();
		});
	});

	async function loadDesired() {
		desired = null;
		desiredErr = '';
		try {
			desired = await api.nodeDesiredState(nodeId);
		} catch (err) {
			desiredErr = errorMessage(err);
		}
	}

	$effect(() => {
		if (tab === 'desired' && desired === null && !desiredErr) loadDesired();
	});

	function openEdit() {
		if (!node) return;
		e = {
			asn: String(node.asn),
			router_id: node.router_id,
			site: node.site ?? '',
			loopback_ipv4: node.loopback_ipv4 ?? '',
			loopback_ipv6: node.loopback_ipv6 ?? '',
			link_local: node.link_local ?? ''
		};
		baseText = JSON.stringify(node.base_template, null, 2);
		labelsText = JSON.stringify(node.labels, null, 2);
		showEdit = true;
	}

	async function saveEdit() {
		if (!baseEditor.valid() || !labelsEditor.valid()) {
			toast.error(t('node.badJson'));
			return;
		}
		saving = true;
		try {
			await api.updateNode(nodeId, {
				asn: Number(e.asn),
				router_id: e.router_id.trim(),
				site: e.site.trim() || null,
				loopback_ipv4: e.loopback_ipv4.trim() || null,
				loopback_ipv6: e.loopback_ipv6.trim() || null,
				link_local: e.link_local.trim() || null,
				base_template: JSON.parse(baseText),
				labels: JSON.parse(labelsText)
			});
			toast.success(t('node.updated'));
			showEdit = false;
			await loadNode();
		} catch (err) {
			toast.error(errorMessage(err));
		} finally {
			saving = false;
		}
	}

	async function lifecycle(action: 'decommission' | 'recommission') {
		const label = action === 'decommission' ? t('node.decommission') : t('node.recommission');
		if (!confirm(t('node.confirmLifecycle', label, nodeId))) return;
		try {
			node =
				action === 'decommission'
					? await api.decommissionNode(nodeId)
					: await api.recommissionNode(nodeId);
			toast.success(t('node.lifecycled', label));
		} catch (err) {
			toast.error(errorMessage(err));
		}
	}

	async function notify(event: string) {
		try {
			const r = await api.notifyNode(nodeId, event, 'manual from UI');
			toast.success(t('node.notified', event, r.delivered, r.subscribers));
		} catch (err) {
			// 409 = server refused because no agent is connected (disconnected node).
			if (err instanceof ApiError && err.status === 409) {
				toast.error(t('disc.refused'));
				await loadHealth();
			} else {
				toast.error(errorMessage(err));
			}
		}
	}

	async function del() {
		if (!confirm(t('node.confirmDelete', nodeId))) return;
		try {
			await api.deleteNode(nodeId);
			toast.success(t('node.deleted'));
			await goto('/nodes');
		} catch (err) {
			toast.error(errorMessage(err));
		}
	}

	const IFACE_EXAMPLE = `{
  "name": "as4242429001",
  "kind": "wireguard",
  "addresses": ["198.18.90.2/31"],
  "peer_routes": ["198.18.90.1/32"],
  "private_key_ref": "secret://${'$'}NODE/as4242429001/private",
  "listen_port": 38001,
  "wireguard_peer": {
    "public_key": "REPLACE_WITH_PEER_PUBLIC_KEY=",
    "allowed_ips": ["198.18.90.1/32"]
  }
}`;
	const BGP_EXAMPLE = `{
  "name": "as4242429001_v4",
  "remote_asn": 4242429001,
  "neighbor": "198.18.90.1",
  "source_address": "198.18.90.2",
  "address_family": "ipv4",
  "interface": "as4242429001",
  "protocol_suffix": "_v4",
  "enabled": true
}`;

	// Structured-form schemas for the spec editor (raw JSON stays available behind
	// an "advanced" toggle). Keys mirror the *_EXAMPLE shapes above.
	const IFACE_FIELDS: SpecField[] = [
		{ key: 'name', label: t('node.f.ifName'), type: 'text', placeholder: 'as4242429001', required: true, mono: true },
		{ key: 'kind', label: t('node.f.ifKind'), type: 'select', options: ['wireguard'] },
		{ key: 'addresses', label: t('node.f.ifAddrs'), type: 'list', placeholder: '198.18.90.2/31' },
		{ key: 'peer_routes', label: t('node.f.ifPeerRoutes'), type: 'list', placeholder: '198.18.90.1/32' },
		{ key: 'listen_port', label: t('node.f.ifPort'), type: 'number', placeholder: '38001' },
		{ key: 'private_key_ref', label: t('node.f.ifKeyRef'), type: 'text', mono: true },
		{
			key: 'wireguard_peer',
			label: t('node.f.ifWgPeer'),
			type: 'group',
			fields: [
				{ key: 'public_key', label: t('node.f.ifWgPub'), type: 'text', mono: true },
				{ key: 'allowed_ips', label: t('node.f.ifWgAllowed'), type: 'list', placeholder: '198.18.90.1/32' }
			]
		}
	];
	const BGP_FIELDS: SpecField[] = [
		{ key: 'name', label: t('node.f.bgpName'), type: 'text', placeholder: 'as4242429001_v4', required: true, mono: true },
		{ key: 'remote_asn', label: t('node.f.bgpAsn'), type: 'number', placeholder: '4242429001', required: true },
		{ key: 'neighbor', label: t('node.f.bgpNeighbor'), type: 'text', placeholder: '198.18.90.1', required: true, mono: true },
		{ key: 'source_address', label: t('node.f.bgpSource'), type: 'text', placeholder: '198.18.90.2', mono: true },
		{ key: 'address_family', label: t('node.f.bgpAf'), type: 'select', options: ['ipv4', 'ipv6'] },
		{ key: 'interface', label: t('node.f.bgpIface'), type: 'text', mono: true },
		{ key: 'protocol_suffix', label: t('node.f.bgpSuffix'), type: 'text', placeholder: '_v4' },
		{ key: 'enabled', label: t('node.f.bgpEnabled'), type: 'bool' }
	];
</script>

<div class="page-head" style="margin-bottom:1rem">
	<div>
		<a href="/nodes" class="back-link"><Icon name="arrow-left" size={13} />{t('node.back')}</a>
		<div class="ph-title">
			<Icon name="nodes" size={22} />
			<h1 class="mono" style="margin:0">{nodeId}</h1>
		</div>
		{#if node}
			<p class="ph-sub mono">
				{node.site ?? '—'} · AS{node.asn} · {t('node.genShort')} {node.current_generation}
			</p>
		{/if}
	</div>
	{#if node}
		<div class="ph-actions">
			{#if health}<HealthBadge value={health} />{/if}
			<span class="badge {node.lifecycle === 'active' ? 'ok' : 'stale'}">
				<span class="dot"></span>{node.lifecycle}
			</span>
		</div>
	{/if}
</div>

{#if severity}
	<div class="disc {severity}" role="alert">
		<span class="disc-ic"><Icon name="alert-triangle" size={18} /></span>
		<div class="disc-body">
			<strong>{severity === 'down' ? t('disc.titleDown') : t('disc.titleStale')}</strong>
			<p>{severity === 'down' ? t('disc.bodyDown') : t('disc.bodyStale')}</p>
		</div>
	</div>
{/if}

{#if loading}
	<div class="empty">{t('common.loading')}</div>
{:else if error}
	<div class="card"><p class="error-text">{error}</p></div>
{:else if node}
	<div class="tabs">
		{#each GROUPS as g (g.id)}
			<button
				class="tab"
				class:active={activeGroup.id === g.id}
				onclick={() => (tab = groupContentId(g))}
			>
				{t(g.key)}
			</button>
		{/each}
	</div>
	{#if activeGroup.tabs.length}
		<div class="subtabs">
			{#each activeGroup.tabs as s (s.id)}
				<button class="subtab" class:active={tab === s.id} onclick={() => (tab = s.id)}>
					{t(s.key)}
				</button>
			{/each}
		</div>
	{/if}

	<div class="card">
		{#if tab === 'overview'}
			<div class="card-head">
				<h3>{t('node.tab.overview')}</h3>
			</div>
			<NodeTrends {nodeId} />
			<NodeSelfMetrics {nodeId} />
			<div class="grid">
				<div><span class="k">{t('node.f.asn')}</span><span class="mono">{node.asn}</span></div>
				<div><span class="k">{t('node.f.routerId')}</span><span class="mono">{node.router_id}</span></div>
				<div><span class="k">{t('node.f.site')}</span><span>{node.site ?? '—'}</span></div>
				<div><span class="k">{t('node.f.lo4')}</span><span class="mono">{node.loopback_ipv4 ?? '—'}</span></div>
				<div><span class="k">{t('node.f.lo6')}</span><span class="mono">{node.loopback_ipv6 ?? '—'}</span></div>
				<div><span class="k">{t('node.f.lla')}</span><span class="mono">{node.link_local ?? '—'}</span></div>
				<div><span class="k">{t('node.f.gen')}</span><span class="mono">{node.current_generation}</span></div>
				<div><span class="k">{t('node.f.created')}</span><span class="faint">{fmtTime(node.created_at)}</span></div>
				<div><span class="k">{t('node.f.updated')}</span><span class="faint">{fmtTime(node.updated_at)}</span></div>
			</div>
			<div class="kvgroup">
				<span class="k">{t('node.f.ipv4')}</span>
				<div>{#each node.ipv4_prefixes as p (p)}<span class="tag">{p}</span>{:else}<span class="faint">{t('common.none')}</span>{/each}</div>
			</div>
			<div class="kvgroup">
				<span class="k">{t('node.f.ipv6')}</span>
				<div>{#each node.ipv6_prefixes as p (p)}<span class="tag">{p}</span>{:else}<span class="faint">{t('common.none')}</span>{/each}</div>
			</div>
			{#if Object.keys(node.labels).length}
				<div class="kvgroup">
					<span class="k">{t('node.f.labels')}</span>
					<div>{#each Object.entries(node.labels) as [k, v] (k)}<span class="tag">{k}={v}</span>{/each}</div>
				</div>
			{/if}

			<div class="card-head section-head">
				<h3 class="muted">{t('node.commonActions')}</h3>
			</div>
			<div class="inline">
				<button class="btn primary sm" onclick={() => (showWizard = true)}>
					<Icon name="route" size={15} />{t('peer.prov.btn')}
				</button>
				<button
					class="btn sm"
					onclick={() => notify('desired_state_updated')}
					disabled={disconnected}
					title={disconnected ? t('disc.pushDisabled') : ''}>{t('node.notifyUpdate')}</button>
				<button
					class="btn sm"
					onclick={() => notify('snapshot_request')}
					disabled={disconnected}
					title={disconnected ? t('disc.pushDisabled') : ''}>
					<Icon name="refresh" size={14} />{t('node.requestSnapshot')}</button>
				<button class="btn sm" onclick={openEdit}><Icon name="edit" size={14} />{t('common.edit')}</button>
			</div>

			<div class="card-head section-head">
				<h3 class="muted">{t('node.dangerZone')}</h3>
			</div>
			<div class="inline">
				{#if node.lifecycle === 'active'}
					<button class="btn sm" onclick={() => lifecycle('decommission')}>{t('node.decommission')}</button>
				{:else}
					<button class="btn sm" onclick={() => lifecycle('recommission')}>{t('node.recommission')}</button>
				{/if}
				<button class="btn sm danger" onclick={del}>{t('node.delete')}</button>
			</div>
		{:else if tab === 'peerings'}
			<PeeringsTab {nodeId} onchange={loadPeerings} />
		{:else if tab === 'interfaces'}
			<SpecResourceTab
				title={t('node.tab.interfaces')}
				singular={t('node.tab.interfaces')}
				fields={{ enabled: true, sortOrder: true, peering: true }}
				example={IFACE_EXAMPLE}
				formFields={IFACE_FIELDS}
				{peerings}
				load={() => api.listInterfaces(nodeId)}
				create={(b) => api.createInterface(nodeId, b)}
				update={(id, b) => api.updateInterface(id, b)}
				remove={(id) => api.deleteInterface(id)}
			/>
		{:else if tab === 'bgp'}
			<SpecResourceTab
				title={t('node.tab.bgp')}
				singular={t('node.tab.bgp')}
				fields={{ sortOrder: true, peering: true }}
				example={BGP_EXAMPLE}
				formFields={BGP_FIELDS}
				{peerings}
				load={() => api.listSessions(nodeId)}
				create={(b) => api.createSession(nodeId, b)}
				update={(id, b) => api.updateSession(id, b)}
				remove={(id) => api.deleteSession(id)}
			/>
		{:else if tab === 'dns'}
			<NodeDnsTab {nodeId} currentGroupId={node?.dns_group_id ?? null} onchange={loadNode} />
		{:else if tab === 'internal'}
			<InternalTopologyTab {nodeId} />
		{:else if tab === 'routing'}
			<RoutingTab {nodeId} />
		{:else if tab === 'tuning'}
			<RouteTuningTab {nodeId} />
		{:else if tab === 'generations'}
			<GenerationsTab {nodeId} onchange={loadNode} />
		{:else if tab === 'status'}
			<StatusEventsTab {nodeId} />
		{:else if tab === 'desired'}
			<div class="card-head">
				<h3>{t('node.currentDesired')}</h3>
				<button class="btn sm icon" onclick={loadDesired} aria-label={t('common.refresh')}>
					<Icon name="refresh" size={15} />
				</button>
			</div>
			{#if desiredErr}
				<p class="error-text">{desiredErr}</p>
			{:else if desired === null}
				<div class="empty">{t('common.loading')}</div>
			{:else}
				<JsonView value={desired} max />
			{/if}
		{:else if tab === 'tokens'}
			<AgentTokensTab {nodeId} />
		{/if}
	</div>
{/if}

<Modal title={t('node.edit')} bind:open={showEdit}>
	<div class="row">
		<label class="field"><span>{t('node.f.asn')}</span><input bind:value={e.asn} /></label>
		<label class="field"><span>{t('node.f.routerId')}</span><input bind:value={e.router_id} /></label>
	</div>
	<div class="row">
		<label class="field"><span>{t('node.f.site')}</span><input bind:value={e.site} /></label>
		<label class="field"><span>{t('node.f.lo4')}</span><input bind:value={e.loopback_ipv4} /></label>
		<label class="field"><span>{t('node.f.lo6')}</span><input bind:value={e.loopback_ipv6} /></label>
		<label class="field"><span>{t('node.f.lla')}</span><input bind:value={e.link_local} placeholder="fe80::28" /></label>
	</div>
	<JsonEditor bind:this={labelsEditor} bind:text={labelsText} label={t('node.f.labels')} rows={4} />
	<JsonEditor bind:this={baseEditor} bind:text={baseText} label={t('nodes.f.base')} rows={12} />
	{#snippet footer()}
		<button class="btn" onclick={() => (showEdit = false)}>{t('common.cancel')}</button>
		<button class="btn primary" onclick={saveEdit} disabled={saving}>
			{saving ? t('common.saving') : t('common.save')}
		</button>
	{/snippet}
</Modal>

{#if showWizard}
	<PeerWizard
		{nodeId}
		bind:open={showWizard}
		oncreated={() => {
			loadPeerings();
			loadNode();
		}}
	/>
{/if}

<style>
	.disc {
		display: flex;
		gap: 0.75rem;
		align-items: flex-start;
		padding: 0.85rem 1rem;
		border-radius: var(--radius);
		margin-bottom: 1.25rem;
		border: 1px solid;
	}
	.disc.stale {
		background: var(--warn-bg);
		border-color: color-mix(in srgb, var(--warn) 40%, transparent);
		color: var(--warn);
	}
	.disc.down {
		background: var(--bad-bg);
		border-color: color-mix(in srgb, var(--down) 50%, transparent);
		color: var(--down);
	}
	.disc-ic {
		font-size: 1.1rem;
		line-height: 1.3;
	}
	.disc-body strong {
		display: block;
		margin-bottom: 0.15rem;
	}
	.disc-body p {
		margin: 0;
		color: var(--text-dim);
		font-size: 0.85rem;
		line-height: 1.5;
	}
	.back-link {
		display: inline-flex;
		align-items: center;
		gap: 0.25rem;
		color: var(--text-faint);
		font-size: 0.8rem;
		margin-bottom: 0.3rem;
	}
	.back-link:hover {
		color: var(--text-dim);
		text-decoration: none;
	}
	.disc-ic {
		display: flex;
		align-items: center;
	}
	.tabs {
		display: flex;
		gap: 2px;
		flex-wrap: wrap;
		margin-bottom: 0.75rem;
		border-bottom: 1px solid var(--border);
	}
	.tab {
		background: none;
		border: none;
		border-bottom: 2px solid transparent;
		color: var(--text-dim);
		padding: 0.55rem 0.9rem;
		cursor: pointer;
		font-size: 0.875rem;
		width: auto;
	}
	.tab:hover {
		color: var(--text);
	}
	.tab.active {
		color: var(--text);
		border-bottom-color: var(--accent);
		font-weight: 500;
	}
	/* secondary nav: pill row under the active group's main tab */
	.subtabs {
		display: flex;
		gap: 0.3rem;
		flex-wrap: wrap;
		margin-bottom: 1rem;
	}
	.subtab {
		background: none;
		border: 1px solid transparent;
		border-radius: var(--radius-sm);
		color: var(--text-dim);
		padding: 0.35rem 0.75rem;
		cursor: pointer;
		font-size: 0.82rem;
		width: auto;
	}
	.subtab:hover {
		color: var(--text);
		background: var(--bg-elev-2);
	}
	.subtab.active {
		color: var(--accent);
		background: var(--accent-soft);
		font-weight: 500;
	}
	/* shared section divider inside the overview (common actions / danger zone) */
	.section-head {
		margin-top: 1.5rem;
		border-top: 1px solid var(--border);
		padding-top: 1rem;
	}
	.grid {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
		gap: 0.85rem 1.5rem;
		margin-bottom: 1rem;
	}
	.grid > div {
		display: flex;
		flex-direction: column;
		gap: 0.15rem;
	}
	.k {
		color: var(--text-faint);
		font-size: 0.72rem;
		text-transform: uppercase;
		letter-spacing: 0.03em;
	}
	.kvgroup {
		margin: 0.6rem 0;
	}
	.kvgroup .k {
		display: block;
		margin-bottom: 0.3rem;
	}
</style>
