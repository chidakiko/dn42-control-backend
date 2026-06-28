<script lang="ts">
	// Step-by-step "add peer" wizard. Collects everything needed to bring a peer
	// up — peering meta, the WireGuard interface and its BGP session — as real
	// form fields (no hand-written JSON), then provisions it.
	//
	// Addressing is single-source by construction: the operator supplies one local
	// link-local (auto-derived from the node's existing peers) and one peer
	// link-local. The interface addresses/peer_routes and the BGP session
	// neighbor/source are all DERIVED from that pair — they can never disagree, so
	// the "source not on interface / neighbor == our own address" class of dead
	// sessions is impossible.
	//
	// The WireGuard key defaults to reusing this node's existing key (one node, one
	// key) rather than a `secret://` placeholder — the placeholder makes the agent
	// mint a separate escrow key that diverges from the published node key.
	//
	// Submit strategy on the deployed backend: the provision endpoint creates
	// peering + interface + the primary session in one transaction; any extra
	// sessions are attached afterwards via createSession using the returned
	// peering_id.
	import { onMount } from 'svelte';
	import { api, errorMessage } from '$lib/api';
	import { toast } from '$lib/toast.svelte';
	import { t } from '$lib/i18n.svelte';
	import type { InterfaceOut } from '$lib/types';
	import Modal from './../Modal.svelte';
	import JsonView from './../JsonView.svelte';
	import Select from './../Select.svelte';

	let {
		nodeId,
		open = $bindable(),
		oncreated
	}: { nodeId: string; open: boolean; oncreated?: () => void } = $props();

	type Family = 'ipv4' | 'ipv6' | 'mp-bgp';
	interface SessionForm {
		id: number;
		family: Family;
		name: string;
		neighbor: string;
		source_address: string;
		iface: string;
		extended_next_hop: boolean;
		enabled: boolean;
	}

	const STEPS = ['peer.wiz.step1', 'peer.wiz.step2', 'peer.wiz.step3', 'peer.wiz.step4'];

	let step = $state(0);
	let submitting = $state(false);
	let nextSid = 1;

	// Auto-derived from the node's existing interfaces (see deriveDefaults).
	let nodeKey = $state(''); // this node's WireGuard private key (plaintext, never shown)
	let nodeLL = $state(''); // link-local this node already uses for external peers

	let basics = $state({
		name: '',
		remote_asn: '',
		is_internal: false,
		remote_node_id: '',
		remote_label: '',
		notes: ''
	});

	let iface = $state({
		name: '',
		listen_port: '',
		mtu: '1420',
		public_key: '',
		endpoint: '',
		keepalive: '',
		allowed_ips: '0.0.0.0/0\n::/0',
		key_mode: 'node' as 'node' | 'custom',
		custom_key: '',
		our_ll: '',
		their_ll: ''
	});

	// Primary BGP session toggles (the session itself is derived, not typed).
	let bgp = $state({
		enabled_session: true,
		family: 'mp-bgp' as Family,
		extended_next_hop: true,
		enabled: true
	});

	let extras = $state<SessionForm[]>([]); // advanced: explicit additional sessions
	let showAdvanced = $state(false);

	// The parent mounts this component only while open ({#if}), so every open is a
	// fresh instance with default $state — no manual reset needed.
	onMount(deriveDefaults);

	async function deriveDefaults() {
		let ifaces: InterfaceOut[] = [];
		try {
			ifaces = await api.listInterfaces(nodeId);
		} catch {
			return; // no defaults — operator fills manually
		}
		const wg = ifaces.filter((i) => i.kind === 'wireguard');
		// Node key: shared across all of this node's interfaces; take the first
		// plaintext (non-placeholder) private_key_ref.
		for (const i of wg) {
			const k = String((i.spec as Record<string, unknown>).private_key_ref ?? '');
			if (k && !k.startsWith('secret://')) {
				nodeKey = k;
				break;
			}
		}
		// House link-local: the fe80:: this node uses on its EXTERNAL peers (names
		// not matching the internal wg-<node>/dn42-* convention). Most common wins.
		const counts = new Map<string, number>();
		for (const i of wg) {
			if (/^wg-/.test(i.name) || /^dn42-/.test(i.name)) continue; // internal links
			const addrs = ((i.spec as Record<string, unknown>).addresses as string[]) ?? [];
			for (const a of addrs) {
				const bare = a.split('/')[0].trim();
				if (bare.toLowerCase().startsWith('fe80:'))
					counts.set(bare, (counts.get(bare) ?? 0) + 1);
			}
		}
		const best = [...counts.entries()].sort((a, b) => b[1] - a[1])[0];
		if (best) {
			nodeLL = best[0];
			iface.our_ll = best[0];
		}
	}

	// --- helpers ---
	const lines = (s: string): string[] =>
		s
			.split(/[\n,]/)
			.map((x) => x.trim())
			.filter(Boolean);
	const numOr = (s: string): number | undefined => {
		const v = s.trim();
		return v ? Number(v) : undefined;
	};
	const isLinkLocal = (a: string): boolean => a.trim().toLowerCase().startsWith('fe80:');

	function onEnterIface() {
		if (!iface.name.trim() && basics.name.trim()) iface.name = basics.name.trim();
	}

	function privateKeyRef(): string {
		return iface.key_mode === 'node' ? nodeKey : iface.custom_key.trim();
	}

	function addExtra(family: Family) {
		const base = iface.name.trim() || basics.name.trim() || 'peer';
		const suffix = family === 'ipv4' ? '_v4' : family === 'ipv6' ? '_v6' : '_mp';
		const ll = family !== 'ipv4';
		extras.push({
			id: nextSid++,
			family,
			name: `${base}${suffix}`,
			neighbor: ll && iface.their_ll.trim() ? `${iface.their_ll.trim()}%${base}` : '',
			source_address: ll ? iface.our_ll.trim() : '',
			iface: ll ? base : '',
			extended_next_hop: family === 'mp-bgp',
			enabled: true
		});
	}
	function removeExtra(id: number) {
		extras = extras.filter((s) => s.id !== id);
	}

	// --- spec builders ---
	function buildPeering(): Record<string, unknown> {
		return {
			name: basics.name.trim(),
			remote_asn: Number(basics.remote_asn),
			remote_node_id: basics.remote_node_id.trim() || null,
			remote_label: basics.remote_label.trim() || null,
			is_internal: basics.is_internal,
			enabled: true,
			notes: basics.notes.trim() || null
		};
	}
	function buildIface(): Record<string, unknown> {
		const ourLL = iface.our_ll.trim();
		const theirLL = iface.their_ll.trim();
		const peer: Record<string, unknown> = {
			public_key: iface.public_key.trim(),
			allowed_ips: lines(iface.allowed_ips)
		};
		if (iface.endpoint.trim()) peer.endpoint = iface.endpoint.trim();
		const ka = numOr(iface.keepalive);
		if (ka !== undefined) peer.persistent_keepalive_seconds = ka;
		const spec: Record<string, unknown> = {
			name: iface.name.trim(),
			kind: 'wireguard',
			mtu: numOr(iface.mtu) ?? 1420,
			// Single-source addressing: our link-local on the interface, the peer's
			// link-local as the on-link route. The session below reuses the same pair.
			addresses: ourLL ? [ourLL] : [],
			peer_routes: theirLL ? [theirLL] : [],
			private_key_ref: privateKeyRef(),
			wireguard_peer: peer
		};
		const lp = numOr(iface.listen_port);
		if (lp !== undefined) spec.listen_port = lp;
		return spec;
	}
	// The primary session is fully derived from the link-local pair — neighbor is
	// the peer LL scoped to the interface, source is our LL. They cannot diverge
	// from the interface because both come from the same two fields.
	function buildPrimarySession(): Record<string, unknown> {
		const name = `${iface.name.trim() || basics.name.trim()}${bgp.family === 'ipv4' ? '_v4' : bgp.family === 'ipv6' ? '_v6' : '_mp'}`;
		const ifname = iface.name.trim();
		const spec: Record<string, unknown> = {
			name,
			remote_asn: Number(basics.remote_asn),
			neighbor: `${iface.their_ll.trim()}%${ifname}`,
			source_address: iface.our_ll.trim(),
			address_family: bgp.family,
			interface: ifname,
			extended_next_hop: bgp.extended_next_hop,
			enabled: bgp.enabled
		};
		return spec;
	}
	function buildExtra(s: SessionForm): Record<string, unknown> {
		const spec: Record<string, unknown> = {
			name: s.name.trim(),
			remote_asn: Number(basics.remote_asn),
			neighbor: s.neighbor.trim(),
			source_address: s.source_address.trim(),
			address_family: s.family,
			extended_next_hop: s.extended_next_hop,
			enabled: s.enabled
		};
		if (s.iface.trim()) spec.interface = s.iface.trim();
		return spec;
	}

	let previewIface = $derived(step === 3 ? buildIface() : null);
	let previewSessions = $derived(
		step === 3
			? [...(bgp.enabled_session ? [buildPrimarySession()] : []), ...extras.map(buildExtra)]
			: []
	);

	// --- step validation / navigation ---
	function validStep(): boolean {
		if (step === 0) {
			if (!basics.name.trim() || !basics.remote_asn.trim()) {
				toast.error(t('peer.wiz.err.basics'));
				return false;
			}
		}
		if (step === 1) {
			if (!iface.name.trim() || !iface.public_key.trim()) {
				toast.error(t('peer.wiz.err.iface'));
				return false;
			}
			if (iface.key_mode === 'node' && !nodeKey) {
				toast.error(t('peer.wiz.err.nodeKey'));
				return false;
			}
			if (iface.key_mode === 'custom' && !iface.custom_key.trim()) {
				toast.error(t('peer.wiz.err.customKey'));
				return false;
			}
			if (!iface.our_ll.trim() || !iface.their_ll.trim()) {
				toast.error(t('peer.wiz.err.ll'));
				return false;
			}
			if (!isLinkLocal(iface.our_ll) || !isLinkLocal(iface.their_ll)) {
				toast.error(t('peer.wiz.err.llFormat'));
				return false;
			}
		}
		if (step === 2) {
			for (const s of extras) {
				if (!s.neighbor.trim() || !s.source_address.trim()) {
					toast.error(t('peer.wiz.err.session'));
					return false;
				}
			}
		}
		return true;
	}
	function next() {
		if (!validStep()) return;
		if (step === 0) onEnterIface();
		step = Math.min(step + 1, STEPS.length - 1);
	}
	function back() {
		step = Math.max(step - 1, 0);
	}

	async function submit() {
		if (!validStep()) return;
		const primary = bgp.enabled_session ? buildPrimarySession() : null;
		const extraSpecs = extras.map(buildExtra);
		const body: Record<string, unknown> = {
			peering: buildPeering(),
			interface_spec: buildIface(),
			interface_enabled: true
		};
		if (primary) body.bgp_spec = primary;
		else if (extraSpecs.length) body.bgp_spec = extraSpecs.shift();

		submitting = true;
		try {
			const res = (await api.provisionPeering(nodeId, body)) as { peering: { id: number } };
			const pid = res.peering.id;
			const failed: string[] = [];
			for (const spec of extraSpecs) {
				try {
					await api.createSession(nodeId, { spec, peering_id: pid });
				} catch (e) {
					failed.push(`${spec.name}: ${errorMessage(e)}`);
				}
			}
			if (failed.length) toast.error(t('peer.wiz.partial', failed.join('; ')));
			else toast.success(t('peer.wiz.done'));
			oncreated?.();
			open = false;
		} catch (e) {
			toast.error(errorMessage(e));
		} finally {
			submitting = false;
		}
	}
</script>

<Modal title={t('peer.prov.title')} bind:open>
	<ol class="steps">
		{#each STEPS as s, i (s)}
			<li class:active={i === step} class:done={i < step}>
				<span class="n">{i + 1}</span>{t(s)}
			</li>
		{/each}
	</ol>

	{#if step === 0}
		<p class="faint hint">{t('peer.wiz.hint')}</p>
		<div class="row">
			<label class="field"><span>{t('peer.f.name')} *</span><input bind:value={basics.name} /></label>
			<label class="field"><span>{t('peer.f.remoteAsn')} *</span><input bind:value={basics.remote_asn} /></label>
		</div>
		<label class="field inline" style="align-items:center; gap:0.5rem">
			<input type="checkbox" bind:checked={basics.is_internal} /> <span style="margin:0">{t('peer.f.internal')}</span>
		</label>
		{#if basics.is_internal}
			<label class="field"><span>{t('peer.f.remoteNode')}</span><input bind:value={basics.remote_node_id} /></label>
		{/if}
		<div class="row">
			<label class="field"><span>{t('peer.f.remoteLabel')}</span><input bind:value={basics.remote_label} /></label>
			<label class="field"><span>{t('peer.f.notes')}</span><input bind:value={basics.notes} /></label>
		</div>
	{:else if step === 1}
		<div class="row">
			<label class="field"><span>{t('peer.wiz.if.name')} *</span><input bind:value={iface.name} /></label>
			<label class="field"><span>{t('peer.wiz.if.listenPort')}</span><input bind:value={iface.listen_port} placeholder="51820" /></label>
			<label class="field"><span>{t('peer.wiz.if.mtu')}</span><input bind:value={iface.mtu} /></label>
		</div>

		<label class="field"><span>{t('peer.wiz.if.pubKey')} *</span><input bind:value={iface.public_key} placeholder="base64 peer public key=" /></label>
		<div class="row">
			<label class="field"><span>{t('peer.wiz.if.endpoint')}</span><input bind:value={iface.endpoint} placeholder="host:port" /></label>
			<label class="field"><span>{t('peer.wiz.if.keepalive')}</span><input bind:value={iface.keepalive} placeholder="25" /></label>
		</div>

		<!-- Key: reuse the node key by default (one node, one key) -->
		<div class="field">
			<span>{t('peer.wiz.if.key')}</span>
			<div class="opts">
				<button
					type="button"
					class="opt"
					class:sel={iface.key_mode === 'node'}
					disabled={!nodeKey}
					onclick={() => (iface.key_mode = 'node')}
				>
					<strong>{t('peer.wiz.if.keyNode')}</strong>
					<small class="faint">{nodeKey ? t('peer.wiz.if.keyNodeOk') : t('peer.wiz.if.keyNodeMissing')}</small>
				</button>
				<button
					type="button"
					class="opt"
					class:sel={iface.key_mode === 'custom'}
					onclick={() => (iface.key_mode = 'custom')}
				>
					<strong>{t('peer.wiz.if.keyCustom')}</strong>
					<small class="faint">{t('peer.wiz.if.keyCustomHint')}</small>
				</button>
			</div>
			{#if iface.key_mode === 'custom'}
				<input class="mt" bind:value={iface.custom_key} placeholder="base64 private key= / secret://..." />
			{/if}
		</div>

		<!-- Link-local pair: the single source for both interface and session addressing -->
		<div class="field">
			<span>{t('peer.wiz.if.llSection')}</span>
			<p class="faint sub">{t('peer.wiz.if.llHint')}</p>
			<div class="row">
				<label class="field">
					<span>{t('peer.wiz.if.ourLL')} *
						{#if nodeLL && iface.our_ll === nodeLL}<em class="tag">{t('peer.wiz.if.auto')}</em>{/if}
					</span>
					<input bind:value={iface.our_ll} placeholder="fe80::28" />
				</label>
				<label class="field"><span>{t('peer.wiz.if.theirLL')} *</span><input bind:value={iface.their_ll} placeholder="fe80::abcd" /></label>
			</div>
		</div>

		<details class="adv" bind:open={showAdvanced}>
			<summary>{t('peer.wiz.adv')}</summary>
			<label class="field mt"><span>{t('peer.wiz.if.allowedIps')}</span><textarea bind:value={iface.allowed_ips} rows="2"></textarea></label>
		</details>
	{:else if step === 2}
		<label class="field inline" style="align-items:center; gap:0.5rem; margin-bottom:0.7rem">
			<input type="checkbox" bind:checked={bgp.enabled_session} /> <span style="margin:0">{t('peer.wiz.bgp.makeSession')}</span>
		</label>

		{#if bgp.enabled_session}
			<div class="sess derived">
				<div class="spread">
					<strong class="mono">{t('peer.wiz.bgp.primary')}</strong>
					<Select
						width="9rem"
						size="sm"
						value={bgp.family}
						options={[
							{ value: 'mp-bgp', label: 'mp-bgp' },
							{ value: 'ipv6', label: 'ipv6' },
							{ value: 'ipv4', label: 'ipv4' }
						]}
						onChange={(v) => (bgp.family = v as Family)}
					/>
				</div>
				<!-- Derived, read-only — proves the addressing is consistent by construction -->
				<dl class="kv">
					<dt>{t('peer.wiz.bgp.neighbor')}</dt><dd class="mono">{iface.their_ll || '—'}%{iface.name || '—'}</dd>
					<dt>{t('peer.wiz.bgp.source')}</dt><dd class="mono">{iface.our_ll || '—'}</dd>
				</dl>
				<div class="inline" style="gap:1rem; flex-wrap:wrap">
					<label class="field inline" style="align-items:center; gap:0.4rem">
						<input type="checkbox" bind:checked={bgp.extended_next_hop} /> <span style="margin:0">{t('peer.wiz.bgp.enh')}</span>
					</label>
					<label class="field inline" style="align-items:center; gap:0.4rem">
						<input type="checkbox" bind:checked={bgp.enabled} /> <span style="margin:0">{t('peer.wiz.bgp.enabled')}</span>
					</label>
				</div>
			</div>
		{:else}
			<p class="faint hint">{t('peer.wiz.bgp.empty')}</p>
		{/if}

		<details class="adv">
			<summary>{t('peer.wiz.bgp.extra')}</summary>
			<div class="inline mt" style="flex-wrap:wrap; margin-bottom:0.6rem">
				<button class="btn sm" onclick={() => addExtra('ipv4')}>+ {t('peer.wiz.bgp.addV4')}</button>
				<button class="btn sm" onclick={() => addExtra('ipv6')}>+ {t('peer.wiz.bgp.addV6')}</button>
				<button class="btn sm" onclick={() => addExtra('mp-bgp')}>+ {t('peer.wiz.bgp.addMp')}</button>
			</div>
			{#each extras as s, i (s.id)}
				<div class="sess">
					<div class="spread">
						<strong class="mono">#{i + 1} · {s.family}</strong>
						<button class="btn ghost sm danger" onclick={() => removeExtra(s.id)}>{t('peer.wiz.bgp.remove')}</button>
					</div>
					<div class="row">
						<label class="field"><span>{t('peer.wiz.bgp.name')}</span><input bind:value={s.name} /></label>
						<label class="field"><span>{t('peer.wiz.bgp.neighbor')} *</span><input bind:value={s.neighbor} placeholder="fe80::2%iface / 172.20.0.1" /></label>
						<label class="field"><span>{t('peer.wiz.bgp.source')} *</span><input bind:value={s.source_address} /></label>
					</div>
				</div>
			{/each}
		</details>
	{:else if step === 3}
		<div class="rev">
			<span class="k">{t('peer.wiz.review.peering')}</span>
			<div class="kvline mono">
				{basics.name} · AS{basics.remote_asn} ·
				{basics.is_internal ? t('peer.internal') : t('peer.external')}
			</div>
		</div>
		<div class="rev">
			<span class="k">{t('peer.wiz.review.iface')}</span>
			<JsonView value={previewIface} max />
		</div>
		<div class="rev">
			<span class="k">{t('peer.wiz.review.sessions')} ({previewSessions.length})</span>
			{#if previewSessions.length}
				<JsonView value={previewSessions} max />
			{:else}
				<p class="faint">{t('peer.wiz.bgp.empty')}</p>
			{/if}
		</div>
	{/if}

	{#snippet footer()}
		<button class="btn" onclick={() => (open = false)}>{t('common.cancel')}</button>
		{#if step > 0}
			<button class="btn" onclick={back}>{t('peer.wiz.back')}</button>
		{/if}
		{#if step < STEPS.length - 1}
			<button class="btn primary" onclick={next}>{t('peer.wiz.next')}</button>
		{:else}
			<button class="btn primary" onclick={submit} disabled={submitting}>
				{submitting ? t('common.saving') : t('peer.wiz.create')}
			</button>
		{/if}
	{/snippet}
</Modal>

<style>
	.steps {
		display: flex;
		gap: 0.4rem;
		list-style: none;
		padding: 0;
		margin: 0 0 1rem;
		flex-wrap: wrap;
	}
	.steps li {
		display: flex;
		align-items: center;
		gap: 0.4rem;
		font-size: 0.8rem;
		color: var(--text-faint);
		padding: 0.2rem 0.5rem;
		border-radius: var(--radius-sm);
	}
	.steps li.active {
		color: var(--text);
		background: var(--accent-soft);
	}
	.steps li.done {
		color: var(--text-dim);
	}
	.steps .n {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 1.25rem;
		height: 1.25rem;
		border-radius: 50%;
		background: var(--border-strong);
		color: var(--text);
		font-size: 0.72rem;
	}
	.steps li.active .n {
		background: var(--accent);
	}
	.hint {
		font-size: 0.8rem;
		margin-top: 0;
	}
	.sub {
		font-size: 0.75rem;
		margin: 0.1rem 0 0.5rem;
	}
	.mt {
		margin-top: 0.5rem;
	}
	/* key-mode / feature option cards */
	.opts {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: 0.5rem;
	}
	.opt {
		display: flex;
		flex-direction: column;
		gap: 0.15rem;
		text-align: left;
		padding: 0.55rem 0.7rem;
		border: 1px solid var(--border);
		border-radius: var(--radius-sm);
		background: var(--surface);
		color: var(--text);
		cursor: pointer;
	}
	.opt:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
	.opt.sel {
		border-color: var(--accent);
		background: var(--accent-soft);
	}
	.opt small {
		font-size: 0.72rem;
	}
	.tag {
		font-style: normal;
		font-size: 0.66rem;
		padding: 0.05rem 0.35rem;
		margin-left: 0.35rem;
		border-radius: 999px;
		background: var(--accent-soft);
		color: var(--accent);
		vertical-align: middle;
	}
	.sess {
		border: 1px solid var(--border);
		border-radius: var(--radius-sm);
		padding: 0.7rem 0.8rem;
		margin-bottom: 0.7rem;
	}
	.sess.derived {
		border-color: color-mix(in srgb, var(--accent) 40%, var(--border));
		background: color-mix(in srgb, var(--accent-soft) 50%, transparent);
	}
	.kv {
		display: grid;
		grid-template-columns: auto 1fr;
		gap: 0.2rem 0.8rem;
		margin: 0.5rem 0;
		font-size: 0.8rem;
	}
	.kv dt {
		color: var(--text-faint);
	}
	.kv dd {
		margin: 0;
	}
	.adv {
		margin-top: 0.4rem;
	}
	.adv summary {
		cursor: pointer;
		font-size: 0.8rem;
		color: var(--text-dim);
	}
	.rev {
		margin-bottom: 0.9rem;
	}
	.rev .k {
		display: block;
		margin-bottom: 0.3rem;
		color: var(--text-faint);
		font-size: 0.72rem;
		text-transform: uppercase;
		letter-spacing: 0.03em;
	}
	.kvline {
		font-size: 0.85rem;
	}
</style>
