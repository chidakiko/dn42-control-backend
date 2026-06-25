<script module lang="ts">
	let counter = 0;
</script>

<script lang="ts">
	// DN42 Control mark: a self-contained rounded app-tile (Cloudflare/Linear-style)
	// in brand orange, holding a minimal routing glyph — one origin node routed out to
	// two peers. Reads as BGP / peering / route distribution. Flat, two-tone, legible
	// on any background and at favicon sizes.
	let { size = 32 }: { size?: number } = $props();

	const u = `dn42mark-${++counter}`;
</script>

<svg
	width={size}
	height={size}
	viewBox="0 0 96 96"
	fill="none"
	xmlns="http://www.w3.org/2000/svg"
	role="img"
	aria-label="DN42 Control"
>
	<defs>
		<linearGradient id="{u}-tile" x1="12" y1="8" x2="84" y2="88" gradientUnits="userSpaceOnUse">
			<stop offset="0" stop-color="#ff8f33" />
			<stop offset="1" stop-color="#e3710f" />
		</linearGradient>
	</defs>

	<!-- app tile -->
	<rect x="6" y="6" width="84" height="84" rx="22" fill="url(#{u}-tile)" />

	<!-- routed paths: origin → two peers -->
	<g stroke="#fff" stroke-width="6.5" fill="none" stroke-linecap="round">
		<path d="M35 48 C 49 48, 51 31, 65 31" />
		<path d="M35 48 C 49 48, 51 65, 65 65" />
	</g>

	<!-- nodes -->
	<circle cx="35" cy="48" r="8.5" fill="#fff" />
	<circle cx="65" cy="31" r="7" fill="#fff" />
	<circle cx="65" cy="65" r="7" fill="#fff" />
</svg>
