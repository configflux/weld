/**
 * API-layer types. Canonical domain shapes (`Item`, `Order`, `ApiError`)
 * live in `@acme/shared-types` and are re-exported here so consumers
 * of the client only need a single import.
 */

export type { Item, Order, ApiError } from "@acme/shared-types";

export interface ApiResponse<T> {
  data: T;
  status: number;
}
