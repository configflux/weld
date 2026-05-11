// ADR 0056 Wave 3 fixture: partial class split across two files.
// The matching half lives in OrderProcessor.Persistence.cs; the
// csharp_msbuild_targets / partial-class merger asserts that both
// pieces produce a single symbol:csharp:Sample.Web.OrderProcessor node.
namespace Sample.Web;

public partial class OrderProcessor
{
    public bool Validate(int orderId)
    {
        return orderId > 0;
    }
}
