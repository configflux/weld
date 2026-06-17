// A second express boundary file: an admin sub-router. Exercises the
// `.all(...)` catch-all pseudo-verb and a PATCH registration so the
// fixture covers more of EXPRESS_VERBS than app.ts alone.
import { Router } from 'express';

const admin = Router();

admin.patch('/settings', updateSettings);
admin.all('/audit', auditMiddleware);

// Inline `export` so tree_sitter mints the canonical file node (see the
// note in app.ts).
export function updateSettings(req: unknown, res: unknown): void {}
export function auditMiddleware(
  req: unknown,
  res: unknown,
  next: () => void,
): void {
  next();
}

export { admin };
