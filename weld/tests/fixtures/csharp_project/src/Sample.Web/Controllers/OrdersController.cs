using System.Threading.Tasks;
using Microsoft.AspNetCore.Mvc;
using Sample.Dal;
using Sample.Dal.Entities;

namespace Sample.Web.Controllers;

[ApiController]
[Route("api/[controller]")]
public class OrdersController : ControllerBase
{
    private readonly OrderDbContext _db;

    public OrdersController(OrderDbContext db)
    {
        _db = db;
    }

    [HttpGet("{id}")]
    public Task<Order?> GetAsync(int id) =>
        Task.FromResult<Order?>(new Order { Id = id, CustomerId = 0 });

    [HttpPost]
    public Task<Order> CreateAsync([FromBody] Order payload) =>
        Task.FromResult(payload);
}
