<script lang="ts">
	// Step-by-step "add peer" wizard. Collects everything needed to bring a peer
	// up — peering meta, the WireGuard interface and 0..N BGP sessions — as real
	// form fields (no hand-written JSON), then provisions it.
	//
	// Submit strategy on the deployed backend: the provision endpoint creates
	// peering + interface + the FIRST session in one transaction; any extra
	// sessions (e.g. a v6 companion) are attached afterwards via createSession
	// using the returned peering_id.
	import { api, errorMessage } from '$lib/api';
	import { toast } from '$lib/toast.svelte';
	import { t } from '$lib/i18n.svelte';
	import Modal from './../Modal.svelte';
	import JsonView from './../JsonView.svelte';

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
		addresses: '',
		private_key_ref: '',
		public_key: '',
		endpoint: '',
		allowed_ips: '',
		keepalive: '',
		peer_routes: ''
	});

	let sessions = $state<SessionForm[]>([]);

	// The parent mounts this component only while open ({#if}), so every open is
	// a fresh instance with default $state — no manual reset needed.

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
	const firstLocal = (): string => (lines(iface.addresses)[0] ?? '').split('/')[0];

	// keep interface + session names aligned with the peer name unless edited
	function onEnterIface() {
		if (!iface.name.trim() && basics.name.trim()) iface.name = basics.name.trim();
		if (!iface.private_key_ref.trim() && iface.name.trim())
			iface.private_key_ref = `secret://$NODE/${iface.name.trim()}/private`;
	}

	function addSession(family: Family, linkLocal: boolean) {
		const base = iface.name.trim() || basics.name.trim() || 'peer';
		const suffix = family === 'ipv4' ? '_v4' : family === 'ipv6' ? '_v6' : '_mp';
		sessions.push({
			id: nextSid++,
			family,
			name: `${base}${suffix}`,
			neighbor: linkLocal ? `fe80::%${base}` : '',
			source_address: linkLocal ? '' : firstLocal(),
			iface: linkLocal ? base : '',
			extended_next_hop: family === 'mp-bgp',
			enabled: true
		});
	}
	function removeSession(id: number) {
		sessions = sessions.filter((s) => s.id !== id);
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
			addresses: lines(iface.addresses),
			peer_routes: lines(iface.peer_routes),
			private_key_ref: iface.private_key_ref.trim(),
			wireguard_peer: peer
		};
		const lp = numOr(iface.listen_port);
		if (lp !== undefined) spec.listen_port = lp;
		return spec;
	}
	function buildSession(s: SessionForm): Record<string, unknown> {
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
	let previewSessions = $derived(step === 3 ? sessions.map(buildSession) : []);

	// --- step validation / navigation ---
	function validStep(): boolean {
		if (step === 0) {
			if (!basics.name.trim() || !basics.remote_asn.trim()) {
				toast.error(t('peer.wiz.err.basics'));
				return false;
			}
		}
		if (step === 1) {
			if (!iface.name.trim() || !iface.public_key.trim() || !lines(iface.addresses).length) {
				toast.error(t('peer.wiz.err.iface'));
				return false;
			}
		}
		if (step === 2) {
			for (const s of sessions) {
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
		const specs = sessions.map(buildSession);
		const body: Record<string, unknown> = {
			peering: buildPeering(),
			interface_spec: buildIface(),
			interface_enabled: true
		};
		if (specs.length) body.bgp_spec = specs[0];

		submitting = true;
		try {
			const res = (await api.provisionPeering(nodeId, body)) as { peering: { id: number } };
			const pid = res.peering.id;
			const failed: string[] = [];
			for (const spec of specs.slice(1)) {
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
		<label class="field"><span>{t('peer.wiz.if.addresses')} *</span><textarea bind:value={iface.addresses} rows="2" placeholder="fe80::1/64"></textarea></label>
		<label class="field"><span>{t('peer.wiz.if.privKey')}</span><input bind:value={iface.private_key_ref} placeholder="secret://$NODE/<iface>/private" /></label>
		<label class="field"><span>{t('peer.wiz.if.pubKey')} *</span><input bind:value={iface.public_key} placeholder="base64 peer public key=" /></label>
		<div class="row">
			<label class="field"><span>{t('peer.wiz.if.endpoint')}</span><input bind:value={iface.endpoint} placeholder="host:port" /></label>
			<label class="field"><span>{t('peer.wiz.if.keepalive')}</span><input bind:value={iface.keepalive} placeholder="25" /></label>
		</div>
		<label class="field"><span>{t('peer.wiz.if.allowedIps')}</span><textarea bind:value={iface.allowed_ips} rows="2" placeholder="0.0.0.0/0&#10;::/0"></textarea></label>
		<label class="field"><span>{t('peer.wiz.if.peerRoutes')}</span><textarea bind:value={iface.peer_routes} rows="2"></textarea></label>
	{:else if step === 2}
		<div class="inline" style="flex-wrap:wrap; margin-bottom:0.6rem">
			<button class="btn sm" onclick={() => addSession('ipv4', false)}>+ {t('peer.wiz.bgp.addV4')}</button>
			<button class="btn sm" onclick={() => addSession('ipv6', true)}>+ {t('peer.wiz.bgp.addV6')}</button>
			<button class="btn sm" onclick={() => addSession('mp-bgp', true)}>+ {t('peer.wiz.bgp.addMp')}</button>
		</div>
		{#if !sessions.length}
			<p class="faint hint">{t('peer.wiz.bgp.empty')}</p>
		{/if}
		{#each sessions as s, i (s.id)}
			<div class="sess">
				<div class="spread">
					<strong class="mono">#{i + 1} · {s.family}</strong>
					<button class="btn ghost sm danger" onclick={() => removeSession(s.id)}>{t('peer.wiz.bgp.remove')}</button>
				</div>
				<div class="row">
					<label class="field"><span>{t('peer.wiz.bgp.name')}</span><input bind:value={s.name} /></label>
					<label class="field"><span>{t('peer.wiz.bgp.family')}</span>
						<select bind:value={s.family}>
							<option value="ipv4">ipv4</option>
							<option value="ipv6">ipv6</option>
							<option value="mp-bgp">mp-bgp</option>
						</select>
					</label>
				</div>
				<div class="row">
					<label class="field"><span>{t('peer.wiz.bgp.neighbor')} *</span><input bind:value={s.neighbor} placeholder="fe80::2%iface / 172.20.0.1" /></label>
					<label class="field"><span>{t('peer.wiz.bgp.source')} *</span><input bind:value={s.source_address} /></label>
				</div>
				<div class="row">
					<label class="field"><span>{t('peer.wiz.bgp.iface')}</span><input bind:value={s.iface} placeholder={iface.name} /></label>
					<label class="field inline" style="align-items:center; gap:0.5rem; margin-top:1.3rem">
						<input type="checkbox" bind:checked={s.extended_next_hop} /> <span style="margin:0">{t('peer.wiz.bgp.enh')}</span>
					</label>
					<label class="field inline" style="align-items:center; gap:0.5rem; margin-top:1.3rem">
						<input type="checkbox" bind:checked={s.enabled} /> <span style="margin:0">{t('peer.wiz.bgp.enabled')}</span>
					</label>
				</div>
			</div>
		{/each}
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
	.sess {
		border: 1px solid var(--border);
		border-radius: var(--radius-sm);
		padding: 0.7rem 0.8rem;
		margin-bottom: 0.7rem;
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
