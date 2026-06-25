<script lang="ts">
	// Shimmer placeholder block. Compose several to mirror a component's real layout
	// while it loads, then crossfade to the content. `w`/`h` accept any CSS length.
	let {
		w = '100%',
		h = '1rem',
		radius = 'var(--radius-sm)',
		circle = false
	}: { w?: string; h?: string; radius?: string; circle?: boolean } = $props();
</script>

<span
	class="sk"
	style="width:{circle ? h : w}; height:{h}; border-radius:{circle ? '50%' : radius}"
></span>

<style>
	.sk {
		display: inline-block;
		position: relative;
		overflow: hidden;
		background: var(--bg-elev-2);
		flex: none;
	}
	.sk::after {
		content: '';
		position: absolute;
		inset: 0;
		transform: translateX(-100%);
		background: linear-gradient(90deg, transparent, color-mix(in srgb, var(--text) 7%, transparent), transparent);
		animation: sk-shimmer 1.4s infinite;
	}
	@keyframes sk-shimmer {
		100% {
			transform: translateX(100%);
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.sk::after {
			animation: none;
		}
	}
</style>
