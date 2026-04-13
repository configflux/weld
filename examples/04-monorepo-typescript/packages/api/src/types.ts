/**
 * Shared API types used across packages.
 */

export interface ApiResponse<T> {
  data: T;
  status: number;
}

export interface ApiError {
  message: string;
  code: string;
}
