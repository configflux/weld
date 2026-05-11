// ADR 0056 Wave 3 fixture: persistence half of partial class
// Sample.Web.OrderProcessor (validation half lives in
// OrderProcessor.Validation.cs). Together they exercise the
// partial-class merger emitted by the C# tree-sitter post-pass.
namespace Sample.Web;

public partial class OrderProcessor
{
    public int Persist(int orderId)
    {
        return orderId;
    }
}
