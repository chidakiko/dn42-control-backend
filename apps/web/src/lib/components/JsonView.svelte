<script lang="ts">
	let { value, max }: { value: unknown; max?: boolean } = $props();
	let text = $derived(JSON.stringify(value, null, 2));
</script>

<pre class="pre" style={max ? '' : 'max-height: 40vh'}>{text}</pre>
