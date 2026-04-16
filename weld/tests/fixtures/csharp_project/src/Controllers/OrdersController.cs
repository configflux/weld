using System.Threading.Tasks;
using Microsoft.AspNetCore.Mvc;

namespace Sample.Api.Controllers;

[ApiController]
[Route("api/[controller]")]
public class OrdersController : ControllerBase
{
    [HttpGet("{id}")]
    public Task<OrderDto> GetAsync(int id) =>
        Task.FromResult(new OrderDto(id, "sample"));

    private string Helper => "ok";
}
