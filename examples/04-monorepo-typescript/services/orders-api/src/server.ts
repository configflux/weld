/**
 * Minimal HTTP service that exposes CRUD for orders.
 *
 * Imports domain types from `@acme/shared-types`, which the web app's
 * `@acme/api` client also imports. This produces the cross-package
 * edge that weld highlights during the 5-minute demo.
 */

import type { Item, Order } from "@acme/shared-types";

const orders = new Map<string, Order>();

export function createOrder(items: Item[]): Order {
  const id = `ord_${orders.size + 1}`;
  const total = items.reduce((sum, i) => sum + i.price, 0);
  const order: Order = {
    id,
    items,
    total,
    createdAt: new Date().toISOString(),
  };
  orders.set(id, order);
  return order;
}

export function getOrder(id: string): Order | undefined {
  return orders.get(id);
}

export function listOrders(): Order[] {
  return Array.from(orders.values());
}
