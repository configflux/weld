/**
 * Shared domain types used by both the web client (`@acme/api`) and the
 * backend `orders-api` service. Keeping the canonical shape here lets
 * weld surface cross-package edges from both importers to a single
 * source of truth.
 */

export interface Item {
  id: number;
  name: string;
  price: number;
}

export interface Order {
  id: string;
  items: Item[];
  total: number;
  createdAt: string;
}

export interface ApiError {
  code: string;
  message: string;
}
