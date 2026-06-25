<script lang="ts">
	import { api, errorMessage } from '$lib/api';
	import { toast } from '$lib/toast.svelte';
	import { t } from '$lib/i18n.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import type { ProvisionOut } from '$lib/types';
	import JsonEditor from '$lib/components/JsonEditor.svelte';
	import JsonView from '$lib/components/JsonView.svelte';

	let stateText = $state('');
	let agentToken = $state('');
	let busy = $state(false);
	let result = $state<ProvisionOut | null>(null);
	let editor: JsonEditor;

	let loadId = $state('');
	let loadingTemplate = $state(false);

	async function loadFromNode() {
		if (!loadId.trim()) return;
		loadingTemplate = true;
		try {
			const ds = await api.nodeDesiredState(loadId.trim());
			stateText = JSON.stringify(ds, null, 2);
			toast.success(t('prov.loaded', loadId.trim()));
		} catch (err) {
			toast.error(errorMessage(err));
		} finally {
			loadingTemplate = false;
		}
	}

	async function submit() {
		if (!editor.valid() || !stateText.trim()) {
			toast.error(t('prov.needValid'));
			return;
		}
		busy = true;
		result = null;
		try {
			const body: Record<string, unknown> = { state: JSON.parse(stateText) };
			if (agentToken.trim()) body.agent_token = agentToken.trim();
			result = await api.provision(body);
			toast.success(t('prov.done', result.node_id, result.generation));
		} catch (err) {
			toast.error(errorMessage(err));
		} finally {
			busy = false;
		}
	}
</script>

<div class="page-head">
	<div>
		<div class="ph-title">
			<Icon name="provision" size={22} />
			<h1>{t('prov.title')}</h1>
		</div>
		<p class="ph-sub">{t('prov.note')}</p>
	</div>
</div>

<div class="card">
	<div class="card-head">
		<h3>{t('prov.fromNode')}</h3>
	</div>
	<div class="inline">
		<input bind:value={loadId} placeholder={t('prov.loadId')} style="max-width:320px" />
		<button class="btn sm" onclick={loadFromNode} disabled={loadingTemplate}>
			{loadingTemplate ? t('prov.loading') : t('prov.loadTemplate')}
		</button>
	</div>
</div>

<div class="card">
	<JsonEditor bind:this={editor} bind:text={stateText} label={t('prov.stateJson')} rows={22} placeholder={'{\n  "schema_version": "v1",\n  "node": { ... },\n  ...\n}'} />
	<label class="field"><span>{t('prov.agentToken')}</span><input bind:value={agentToken} /></label>
	<button class="btn primary" onclick={submit} disabled={busy}>
		{busy ? t('prov.submitting') : t('prov.submit')}
	</button>
</div>

{#if result}
	<div class="card">
		<h3>{t('prov.result')}</h3>
		<JsonView value={result} max />
	</div>
{/if}
