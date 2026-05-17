// Sample.Dal.csproj declares <Compile Remove="Excluded.cs" /> so this file is NOT a member of
// Sample.Dal's compilation set. The ADR 0056 addendum tests assert that no csproj -> contains ->
// file edge is emitted for this path. Real-world analogue: a sub-tree that is compiled by a
// different project (multi-targeting carve-outs, source generator scratchpads, etc.).

namespace Sample.Dal.Excluded;

public static class ExcludedStub
{
    public static int Sentinel() => 0;
}
