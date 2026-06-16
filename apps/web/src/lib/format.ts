// Display helpers shared across pages.
//
// The control server emits a mix of ISO timestamps: some carry a zone offset
// (…+00:00 / …Z), some are naive (…T13:48:04.266733) but are actually UTC.
// JS parses a naive ISO string as *local* time, which would be wrong, so we
// append 'Z' when there's no zone. Everything is then rendered in the browser's
// local timezone via toLocale*.

import { locale } from './i18n.svelte';

function parse(value: string | null | undefined): Date | null {
	if (!value) return null;
	let s = value.trim();
	// Has explicit zone? (Z, +hh:mm, -hh:mm at the end of the time part)
	const hasZone = /[zZ]$/.test(s) || /[+-]\d\d:?\d\d$/.test(s);
	if (!hasZone) s = s + 'Z';
	const d = new Date(s);
	return isNaN(d.getTime()) ? null : d;
}

export function fmtTime(value: string | null | undefined): string {
	const d = parse(value);
	if (!d) return value ? value : '—';
	// Uses the browser's locale + timezone automatically.
	return d.toLocaleString(locale.tag);
}

export function relTime(value: string | null | undefined): string {
	const d = parse(value);
	if (!d) return value ? value : '—';
	const secs = Math.round((Date.now() - d.getTime()) / 1000);
	const t = locale.dict.rel;
	if (secs < 0) return t.now;
	if (secs < 60) return t.s(secs);
	const mins = Math.floor(secs / 60);
	if (mins < 60) return t.m(mins);
	const hrs = Math.floor(mins / 60);
	if (hrs < 24) return t.h(hrs);
	const days = Math.floor(hrs / 24);
	return t.d(days);
}
