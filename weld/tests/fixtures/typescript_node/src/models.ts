export interface Item {
  id: number;
  name: string;
  createdAt: Date;
}

export interface User {
  id: number;
  email: string;
  items: Item[];
}
