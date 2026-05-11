using System.Threading.Tasks;
using Xunit;
using Sample.Dal;
using Sample.Web.Controllers;

namespace Sample.Tests;

public class OrdersControllerTests
{
    [Fact]
    public async Task Get_returns_order_with_supplied_id()
    {
        var controller = new OrdersController(new OrderDbContext());
        var result = await controller.GetAsync(7);
        Assert.NotNull(result);
        Assert.Equal(7, result!.Id);
    }

    [Fact]
    public async Task Post_round_trips_payload()
    {
        var controller = new OrdersController(new OrderDbContext());
        var posted = new Sample.Dal.Entities.Order { Id = 1, CustomerId = 2 };
        var echoed = await controller.CreateAsync(posted);
        Assert.Equal(1, echoed.Id);
    }
}
