// Picked up by the implicit SDK glob even though the csproj has no
// <Compile Include="Controllers/*.cs"/> directive.
using Microsoft.AspNetCore.Mvc;
using System.Threading.Tasks;

namespace SdkFixture.WebApp.Controllers;

[ApiController]
[Route("api/[controller]")]
public class ProductsController : ControllerBase
{
    [HttpGet("{id}")]
    public Task<ProductDto> Get(int id) => Task.FromResult(new ProductDto(id, "Sample"));

    [HttpPost]
    public Task<ProductDto> Create([FromBody] ProductDto product) => Task.FromResult(product);
}

public record ProductDto(int Id, string Name);
