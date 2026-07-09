import { apiGet, apiPost } from "./http";

export function createMemory(payload: Record<string, unknown>) {
  return apiPost<Record<string, unknown>>("/api/v1/memory/notes", payload);
}

export function searchMemory(payload: Record<string, unknown>) {
  return apiPost<Record<string, unknown>>("/api/v1/memory/search", payload);
}

export function getMemoryNote(noteId: number) {
  return apiGet<Record<string, unknown>>(`/api/v1/memory/notes/${noteId}`);
}

export function createMemoryLink(payload: Record<string, unknown>) {
  return apiPost<Record<string, unknown>>("/api/v1/memory/links", payload);
}

export function buildReflectionFromReview(reviewId: number) {
  return apiPost<Record<string, unknown>>(`/api/v1/memory/reflection/from-review/${reviewId}`);
}

export function buildPlaybookFromReview(reviewId: number) {
  return apiPost<Record<string, unknown>>(`/api/v1/memory/playbook/from-review/${reviewId}`);
}

export function listPlaybooks() {
  return apiGet<Record<string, unknown>>("/api/v1/memory/playbooks");
}

export function disableMemory(noteId: number, reason?: string) {
  return apiPost<Record<string, unknown>>(`/api/v1/memory/notes/${noteId}/disable`, { reason });
}

export function markConflict(noteId: number, status: string, reason?: string) {
  return apiPost<Record<string, unknown>>(`/api/v1/memory/notes/${noteId}/conflict`, { status, reason });
}

export function getMemoryConfig() {
  return apiGet<Record<string, unknown>>("/api/v1/memory/config");
}
