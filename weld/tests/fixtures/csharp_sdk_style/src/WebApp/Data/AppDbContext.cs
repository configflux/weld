// EF Core DbContext + DbSet -- the csharp_efcore strategy must
// discover this entity even though the csproj has no explicit
// <Compile Include> entries.
using Microsoft.EntityFrameworkCore;

namespace SdkFixture.WebApp.Data;

public class AppDbContext : DbContext
{
    public AppDbContext(DbContextOptions<AppDbContext> options) : base(options) { }
    public DbSet<Product> Products { get; set; } = null!;
}

public class Product
{
    public int Id { get; set; }
    public string Name { get; set; } = "";
}
