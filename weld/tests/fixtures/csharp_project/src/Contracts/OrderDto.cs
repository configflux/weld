namespace Sample.Api.Contracts;

public interface IOrderReader
{
    Task<OrderDto> GetAsync(int id);
}

public struct OrderId
{
    public int Value { get; init; }
}

public record OrderDto(int Id, string Name);
