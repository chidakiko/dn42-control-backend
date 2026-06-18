// Tiny toast notification store (runes-based). Components call
// `toast.success(...)` / `toast.error(...)`; <Toaster/> renders the queue.

export type ToastKind = 'success' | 'error' | 'info';

export interface Toast {
	id: number;
	kind: ToastKind;
	message: string;
}

let counter = 0;

class ToastStore {
	items = $state<Toast[]>([]);

	private push(kind: ToastKind, message: string, ttl: number) {
		const id = ++counter;
		this.items.push({ id, kind, message });
		setTimeout(() => this.dismiss(id), ttl);
	}

	success(message: string) {
		this.push('success', message, 3500);
	}
	error(message: string) {
		this.push('error', message, 6000);
	}
	info(message: string) {
		this.push('info', message, 3500);
	}

	dismiss(id: number) {
		this.items = this.items.filter((t) => t.id !== id);
	}
}

export const toast = new ToastStore();
