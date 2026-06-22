<script lang="ts">
	import { untrack } from 'svelte';
	import { api, errorMessage } from '$lib/api';
	import { toast } from '$lib/toast.svelte';
	import { t } from '$lib/i18n.svelte';
	import type { DnsRecordOut } from '$lib/types';
	import Modal from '$lib/components/Modal.svelte';

	let { gid, zid, zone }: { gid: number; zid: number; zone: string } = $props();

	const TYPES = ['A', 'AAAA', 'CNAME', 'NS', 'PTR', 'TXT', 'MX', 'SRV', 'CAA'];

	let records = $state<DnsRecordOut[]>([]);
	let loading = $state(true);
	let error = $state('');

	let showForm = $state(false);
	let editing = $state<DnsRecordOut | null>(null);
	let saving = $state(false);
	let fName = $state('@');
	let fType = $state('A');
	let fContent = $state('');
	let fTtl = $state('');
	let fComment = $state('');
	let fEnabled = $state(true);
	let ptrIp = $state('');

	async function load() {
		loading = true;
		error = '';
		try {
			records = await api.listZoneRecords(gid, zid);
		} catch (e) {
			error = errorMessage(e);
		} finally {
			loading = false;
		}
	}
	$effect(() => {
		zid;
		untrack(() => load());
	});

	function openCreate() {
		editing = null;
		fName = '@';
		fType = 'A';
		fContent = '';
		fTtl = '';
		fComment = '';
		fEnabled = true;
		ptrIp = '';
		showForm = true;
	}

	function openEdit(r: DnsRecordOut) {
		editing = r;
		fName = r.name;
		fType = r.type;
		fContent = r.content;
		fTtl = r.ttl == null ? '' : String(r.ttl);
		fComment = r.comment ?? '';
		fEnabled = r.enabled;
		ptrIp = '';
		showForm = true;
	}

	// rDNS 助手：把 IP 算成反向名（FQDN），填进记录 name。
	function reverseName(ip: string): string | null {
		const v = ip.trim();
		if (v.includes(':')) {
			// IPv6：展开成 32 个 nibble，倒序 + .ip6.arpa
			const parts = v.split('::');
			if (parts.length > 2) return null;
			const head = parts[0] ? parts[0].split(':') : [];
			const tail = parts.length === 2 && parts[1] ? parts[1].split(':') : [];
			const missing = 8 - head.length - tail.length;
			if (missing < 0) return null;
			const groups = [...head, ...Array(parts.length === 2 ? missing : 0).fill('0'), ...tail];
			if (groups.length !== 8) return null;
			let nibbles = '';
			for (const g of groups) {
				if (!/^[0-9a-fA-F]{1,4}$/.test(g)) return null;
				nibbles += g.padStart(4, '0').toLowerCase();
			}
			return nibbles.split('').reverse().join('.') + '.ip6.arpa';
		}
		const octets = v.split('.');
		if (octets.length !== 4 || octets.some((o) => !/^\d{1,3}$/.test(o) || Number(o) > 255))
			return null;
		return octets.reverse().join('.') + '.in-addr.arpa';
	}

	function fillPtr() {
		const rev = reverseName(ptrIp);
		if (!rev) {
			toast.error(t('dns.rec.ptrBadIp'));
			return;
		}
		fName = rev;
		fType = 'PTR';
	}

	function body(): Record<string, unknown> {
		return {
			name: fName.trim(),
			type: fType,
			content: fContent.trim(),
			ttl: fTtl.trim() === '' ? null : Number(fTtl),
			comment: fComment.trim() === '' ? null : fComment.trim(),
			enabled: fEnabled
		};
	}

	async function save() {
		saving = true;
		try {
			if (editing) {
				await api.updateZoneRecord(gid, zid, editing.id, body());
				toast.success(t('dns.rec.updated'));
			} else {
				await api.createZoneRecord(gid, zid, body());
				toast.success(t('dns.rec.created'));
			}
			showForm = false;
			await load();
		} catch (e) {
			toast.error(errorMessage(e));
		} finally {
			saving = false;
		}
	}

	async function remove(r: DnsRecordOut) {
		if (!confirm(t('dns.rec.confirmDelete', `${r.name} ${r.type}`))) return;
		try {
			await api.deleteZoneRecord(gid, zid, r.id);
			toast.success(t('dns.rec.deleted'));
			await load();
		} catch (e) {
			toast.error(errorMessage(e));
		}
	}
</script>

<div class="spread" style="margin-bottom:0.6rem">
	<h3 style="margin:0">{t('dns.rec.title')} · <span class="mono faint">{zone}</span></h3>
	<button class="btn sm primary" onclick={openCreate}>+ {t('dns.rec.new')}</button>
</div>

{#if loading}
	<div class="empty">{t('common.loading')}</div>
{:else if error}
	<p class="error-text">{error}</p>
{:else if records.length === 0}
	<div class="empty">{t('dns.rec.empty')}</div>
{:else}
	<table>
		<thead>
			<tr>
				<th>{t('dns.rec.col.name')}</th>
				<th>{t('dns.rec.col.type')}</th>
				<th>{t('dns.rec.col.content')}</th>
				<th>{t('dns.rec.col.ttl')}</th>
				<th>{t('dns.rec.col.comment')}</th>
				<th></th>
			</tr>
		</thead>
		<tbody>
			{#each records as r (r.id)}
				<tr class:disabled-row={!r.enabled}>
					<td class="mono">{r.name}</td>
					<td><span class="badge">{r.type}</span></td>
					<td class="mono" style="font-size:0.82rem">{r.content}</td>
					<td class="faint">{r.ttl ?? '—'}</td>
					<td class="faint" style="font-size:0.82rem">{r.comment ?? ''}</td>
					<td class="actions">
						<button class="btn ghost sm" onclick={() => openEdit(r)}>{t('common.edit')}</button>
						<button class="btn ghost sm danger" onclick={() => remove(r)}>✕</button>
					</td>
				</tr>
			{/each}
		</tbody>
	</table>
{/if}

<Modal title={editing ? t('dns.rec.edit') : t('dns.rec.new')} bind:open={showForm}>
	<div class="form">
		<label>
			<span>{t('dns.rec.field.type')}</span>
			<select bind:value={fType}>
				{#each TYPES as ty (ty)}
					<option value={ty}>{ty}</option>
				{/each}
			</select>
		</label>
		<label>
			<span>{t('dns.rec.field.name')}</span>
			<input bind:value={fName} placeholder="@ / www / ns1" />
		</label>
		<label>
			<span>{t('dns.rec.field.content')}</span>
			<input bind:value={fContent} placeholder="fdce:1111:2222::1" />
		</label>
		{#if fType === 'PTR'}
			<div class="ptr">
				<span class="faint">{t('dns.rec.ptr')}</span>
				<input bind:value={ptrIp} placeholder="172.20.0.1 / fdce::1" />
				<button class="btn ghost sm" type="button" onclick={fillPtr}>{t('dns.rec.ptrFromIp')}</button>
			</div>
		{/if}
		<label>
			<span>{t('dns.rec.field.ttl')}</span>
			<input type="number" bind:value={fTtl} min="0" placeholder={t('dns.rec.ttlAuto')} />
		</label>
		<label>
			<span>{t('dns.rec.field.comment')}</span>
			<input bind:value={fComment} />
		</label>
		<label class="inline">
			<input type="checkbox" bind:checked={fEnabled} />
			<span>{t('dns.rec.field.enabled')}</span>
		</label>
	</div>
	{#snippet footer()}
		<button class="btn ghost" onclick={() => (showForm = false)}>{t('common.cancel')}</button>
		<button class="btn primary" onclick={save} disabled={saving}>{t('common.save')}</button>
	{/snippet}
</Modal>

<style>
	.form {
		display: flex;
		flex-direction: column;
		gap: 0.7rem;
	}
	.form label {
		display: flex;
		flex-direction: column;
		gap: 0.3rem;
	}
	.form label.inline {
		flex-direction: row;
		align-items: center;
		gap: 0.5rem;
	}
	.ptr {
		display: flex;
		align-items: center;
		gap: 0.5rem;
	}
	.disabled-row {
		opacity: 0.5;
	}
</style>
