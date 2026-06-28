<script lang="ts">
	// 路由调优 tab：把三个"社区 / 选路"旋钮做成表单，免手填原始 JSON。
	//   - link_latency（会话级，spec）：打 DN42 latency 社区，仅可见性/信令。
	//   - cold_potato_med（节点级，base_template.bird）：同区域偏好 MED。
	//   - route_local_pref（节点级）：per-prefix local-pref，精细路由调优、零透传爆炸半径。
	import { onMount } from 'svelte';
	import { api, errorMessage } from '$lib/api';
	import { toast } from '$lib/toast.svelte';
	import { t } from '$lib/i18n.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import Select from '$lib/components/Select.svelte';
	import SkeletonText from '$lib/components/SkeletonText.svelte';

	let { nodeId }: { nodeId: string } = $props();

	let loading = $state(true);
	let err = $state('');

	// 节点级（base_template.bird）
	let baseTemplate = $state<Record<string, unknown>>({});
	let coldPotatoMed = $state<number | ''>(50);
	let rules = $state<{ prefix: string; local_pref: number | '' }[]>([]);
	let savingNode = $state(false);

	// 会话级 link_latency
	type SessRow = { id: number; name: string; remote_asn: number; lat: number | '' };
	let sessRows = $state<SessRow[]>([]);
	let savingSid = $state<number | null>(null);

	// DN42 latency 档（1-9，对数分档）→ 下拉标签。
	const LAT_TIERS: { v: number; label: string }[] = [
		{ v: 1, label: '1 · ≤2.7ms' },
		{ v: 2, label: '2 · ≤7.3ms' },
		{ v: 3, label: '3 · ≤20ms' },
		{ v: 4, label: '4 · ≤55ms' },
		{ v: 5, label: '5 · ≤148ms' },
		{ v: 6, label: '6 · ≤403ms' },
		{ v: 7, label: '7 · ≤1097ms' },
		{ v: 8, label: '8 · ≤2981ms' },
		{ v: 9, label: '9 · >2981ms' }
	];

	function num(v: unknown, fallback: number | ''): number | '' {
		return typeof v === 'number' ? v : fallback;
	}

	async function load() {
		loading = true;
		err = '';
		try {
			const node = await api.getNode(nodeId);
			baseTemplate = { ...(node.base_template ?? {}) };
			const bird = (baseTemplate.bird ?? {}) as Record<string, unknown>;
			coldPotatoMed = num(bird.cold_potato_med, 50);
			const rlp = Array.isArray(bird.route_local_pref) ? bird.route_local_pref : [];
			rules = rlp.map((r) => {
				const o = r as Record<string, unknown>;
				return { prefix: String(o.prefix ?? ''), local_pref: num(o.local_pref, 200) };
			});
			const sessions = await api.listSessions(nodeId);
			sessRows = sessions.map((s) => ({
				id: s.id,
				name: s.name,
				remote_asn: s.remote_asn,
				lat: num(s.spec.link_latency, '')
			}));
		} catch (e) {
			err = errorMessage(e);
		} finally {
			loading = false;
		}
	}
	onMount(load);

	function addRule() {
		rules = [...rules, { prefix: '', local_pref: 200 }];
	}
	function removeRule(i: number) {
		rules = rules.filter((_, idx) => idx !== i);
	}

	async function saveNode() {
		savingNode = true;
		try {
			const bird = { ...((baseTemplate.bird ?? {}) as Record<string, unknown>) };
			bird.cold_potato_med = coldPotatoMed === '' ? 50 : Number(coldPotatoMed);
			bird.route_local_pref = rules
				.filter((r) => r.prefix.trim())
				.map((r) => ({
					prefix: r.prefix.trim(),
					local_pref: r.local_pref === '' ? 100 : Number(r.local_pref)
				}));
			const next = { ...baseTemplate, bird };
			await api.updateNode(nodeId, { base_template: next });
			baseTemplate = next;
			toast.success(t('rt.savedNode'));
		} catch (e) {
			toast.error(errorMessage(e));
		} finally {
			savingNode = false;
		}
	}

	async function saveSession(row: SessRow) {
		savingSid = row.id;
		try {
			// 取当前 spec，仅改 link_latency，其余原样回写。
			const sessions = await api.listSessions(nodeId);
			const cur = sessions.find((s) => s.id === row.id);
			if (!cur) throw new Error('session gone');
			const spec = { ...cur.spec, link_latency: row.lat === '' ? null : Number(row.lat) };
			await api.updateSession(row.id, { spec });
			toast.success(t('rt.savedSession'));
		} catch (e) {
			toast.error(errorMessage(e));
		} finally {
			savingSid = null;
		}
	}
</script>

{#if loading}
	<SkeletonText lines={5} />
{:else if err}
	<p class="error-text">{err}</p>
{:else}
	<!-- 节点级：cold_potato_med + route_local_pref -->
	<div class="card-head">
		<h3>{t('rt.node.title')}</h3>
		<button class="btn primary sm" onclick={saveNode} disabled={savingNode}>
			{savingNode ? t('common.saving') : t('common.save')}
		</button>
	</div>
	<p class="faint hint">{t('rt.node.hint')}</p>

	<label class="field">
		<span>{t('rt.coldPotato')}</span>
		<input type="number" min="0" bind:value={coldPotatoMed} style="max-width:8rem" />
	</label>
	<p class="faint hint">{t('rt.coldPotato.hint')}</p>

	<div class="card-head" style="margin-top:1rem">
		<h4>{t('rt.rlp.title')}</h4>
		<button class="btn ghost sm" onclick={addRule}>+ {t('rt.rlp.add')}</button>
	</div>
	<p class="faint hint">{t('rt.rlp.hint')}</p>
	{#if rules.length === 0}
		<div class="empty">{t('rt.rlp.empty')}</div>
	{:else}
		<table>
			<thead>
				<tr><th>{t('rt.rlp.prefix')}</th><th>{t('rt.rlp.localpref')}</th><th></th></tr>
			</thead>
			<tbody>
				{#each rules as rule, i (i)}
					<tr>
						<td><input class="mono" bind:value={rule.prefix} placeholder="172.20.62.160/27" /></td>
						<td><input type="number" min="0" bind:value={rule.local_pref} style="max-width:7rem" /></td>
						<td class="actions">
							<button class="btn ghost sm danger" onclick={() => removeRule(i)}>{t('common.delete')}</button>
						</td>
					</tr>
				{/each}
			</tbody>
		</table>
	{/if}

	<!-- 会话级 link_latency -->
	<div class="card-head" style="margin-top:1.6rem">
		<h3>{t('rt.sess.title')}</h3>
		<button class="btn sm icon" onclick={load} disabled={loading} aria-label={t('common.refresh')}><Icon name="refresh" size={15} /></button>
	</div>
	<p class="faint hint">{t('rt.sess.hint')}</p>
	{#if sessRows.length === 0}
		<div class="empty">{t('rt.sess.empty')}</div>
	{:else}
		<table>
			<thead>
				<tr><th>{t('rt.sess.name')}</th><th>{t('rt.sess.asn')}</th><th>{t('rt.sess.lat')}</th><th></th></tr>
			</thead>
			<tbody>
				{#each sessRows as row (row.id)}
					<tr>
						<td class="mono">{row.name}</td>
						<td class="mono faint">{row.remote_asn}</td>
						<td>
							<Select
								width="11rem"
								size="sm"
								value={row.lat === '' ? '' : String(row.lat)}
								options={[
									{ value: '', label: t('rt.sess.unset') },
									...LAT_TIERS.map((tier) => ({ value: String(tier.v), label: tier.label }))
								]}
								onChange={(v) => (row.lat = v === '' ? '' : Number(v))}
							/>
						</td>
						<td class="actions">
							<button class="btn ghost sm" onclick={() => saveSession(row)} disabled={savingSid === row.id}>
								{savingSid === row.id ? t('common.saving') : t('common.save')}
							</button>
						</td>
					</tr>
				{/each}
			</tbody>
		</table>
	{/if}
{/if}

<style>
	.hint {
		margin: -0.2rem 0 0.6rem;
		font-size: 0.85em;
		line-height: 1.5;
	}
</style>
