<script lang="ts">
	// A textarea bound to a JSON string with live validity feedback. The parent
	// reads `text`; call `parse()` (exported) to get the value or throw.
	import { t } from '$lib/i18n.svelte';

	let {
		text = $bindable(''),
		label = 'JSON',
		rows = 12,
		placeholder = ''
	}: { text?: string; label?: string; rows?: number; placeholder?: string } = $props();

	let error = $derived.by(() => {
		if (!text.trim()) return null;
		try {
			JSON.parse(text);
			return null;
		} catch (e) {
			return (e as Error).message;
		}
	});

	export function valid(): boolean {
		return error === null;
	}
</script>

<label class="field">
	<span>{label}</span>
	<textarea bind:value={text} {rows} {placeholder} spellcheck="false"></textarea>
	{#if error}
		<span class="error-text">{t('json.invalid', error)}</span>
	{/if}
</label>
