/**
 * Typed API client for the backend service.
 */

export interface Item {
  id: number;
  name: string;
  price: number;
}

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
