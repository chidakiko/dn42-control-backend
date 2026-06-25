<script lang="ts">
	// Read-only JSON viewer with lightweight syntax highlighting (keys / strings /
	// numbers / booleans / null). Input is trusted control-plane data; we still
	// HTML-escape before tokenising so values can't inject markup.
	let { value, max }: { value: unknown; max?: boolean } = $props();

	const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

	let html = $derived.by(() => {
		const json = esc(JSON.stringify(value, null, 2) ?? '');
		return json.replace(
			/("(?:\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(?:true|false)\b|\bnull\b|-?\d+(?:\.\d*)?(?:[eE][+-]?\d+)?)/g,
			(m) => {
				let cls = 'jh-num';
				if (/^"/.test(m)) cls = /:\s*$/.test(m) ? 'jh-key' : 'jh-str';
				else if (m === 'true' || m === 'false') cls = 'jh-bool';
				else if (m === 'null') cls = 'jh-null';
				return `<span class="${cls}">${m}</span>`;
			}
		);
	});
</script>

<pre class="pre json-view" style={max ? '' : 'max-height: 40vh'}>{@html html}</pre>

<style>
	.json-view :global(.jh-key) {
		color: #4c9aff;
	}
	.json-view :global(.jh-str) {
		color: var(--ok);
	}
	.json-view :global(.jh-num) {
		color: var(--warn);
	}
	.json-view :global(.jh-bool) {
		color: var(--accent);
		font-weight: 600;
	}
	.json-view :global(.jh-null) {
		color: var(--text-faint);
	}
</style>
