/**
 * Route handlers for the orders service. Kept thin -- the demo
 * cares about shape (handler -> shared types), not a real HTTP stack.
 */

import type { Item } from "@acme/shared-types";
import { createOrder, getOrder, listOrders } from "./server";

export function postOrders(body: { items: Item[] }) {
  return createOrder(body.items);
}

export function getOrders() {
  return listOrders();
}

export function getOrderById(id: string) {
  return getOrder(id);
}
