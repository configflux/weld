using System.Threading.Tasks;
using Microsoft.AspNetCore.Mvc;

namespace Sample.Web.Controllers;

[ApiController]
[Route("api/[controller]")]
public class OrdersController : ControllerBase
{
    [HttpGet("{id}")]
    public Task<int> GetAsync(int id) => Task.FromResult(id);
}
