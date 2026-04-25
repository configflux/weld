/**
 * Typed API client for the backend service.
 *
 * Domain types come from `@acme/shared-types`, which the `orders-api`
 * service also imports. weld picks up the cross-package edge from this
 * file to shared-types, so client and server share one canonical shape.
 * Consumers that need `Item` re-exported should import it from
 * `@acme/api`'s `types` barrel.
 */

import type { Item } from "@acme/shared-types";

export async function fetchItems(baseUrl: string): Promise<Item[]> {
  const res = await fetch(`${baseUrl}/items`);
  return res.json() as Promise<Item[]>;
}

export async function createItem(
  baseUrl: string,
  name: string,
  price: number,
): Promise<Item> {
  const res = await fetch(`${baseUrl}/items`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, price }),
  });
  return res.json() as Promise<Item>;
}
