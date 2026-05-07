import { add, double } from "./lib";

describe("lib", () => {
  it("adds", () => {
    expect(add(2, 3)).toBe(5);
  });
  it("doubles", () => {
    expect(double(7)).toBe(14);
  });
});
