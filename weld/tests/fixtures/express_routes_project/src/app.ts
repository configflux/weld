// A route-emitting fixture for the express strategy (ADR 0064 criterion
// 3, TypeScript / JS). It exercises the express handler-registration
// callsite grammars the strategy recognises: direct
// `app.<verb>("/p", h)` / `router.<verb>("/p", h)` registrations and the
// chained `app.route("/p").get(h).post(h)` form. Kept intentionally
// small and static. `@types/express` is a devDependency so the import is
// type-resolvable; the strategy itself is regex-based and needs only the
// `from 'express'` import to fire.
import express from 'express';
import { Router } from 'express';

const app = express();
const router = Router();

// Direct verb-call registrations on distinct paths.
app.get('/health', (req, res) => res.send('ok'));
app.post('/users', createUser);

// A router (sub-app) registration.
router.delete('/users/:id', removeUser);

// Chained route form: one path, two HTTP methods.
app.route('/items')
   .get(listItems)
   .post(createItem);

// A wildcard capture path, taken verbatim.
app.get('/assets/*', serveAsset);

// A commented-out registration must NOT mint a route.
// app.get('/disabled', disabled);

// `app.use` is middleware, not a route verb -> no route minted.
app.use('/static', express.static('public'));

// `app.get` as a settings *getter* (one non-path argument) must NOT mint
// a route -- the leading-slash path guard drops it.
const engine = app.get('view engine');

// Inline `export` on the handlers so the tree_sitter strategy mints a
// canonical `file:` node carrying `imports_from` (the graph-derived
// express marker) -- a bare `export { ... }` re-export list is not
// captured by the exports query and would leave only the express
// boundary-file placeholder.
export function createUser(req: unknown, res: unknown): void {}
export function removeUser(req: unknown, res: unknown): void {}
export function listItems(req: unknown, res: unknown): void {}
export function createItem(req: unknown, res: unknown): void {}
export function serveAsset(req: unknown, res: unknown): void {}

export { app, router, engine };
