using Microsoft.EntityFrameworkCore;
using Sample.Dal.Entities;

namespace Sample.Dal;

public class OrderDbContext : DbContext
{
    public DbSet<Order> Orders { get; set; } = null!;
    public DbSet<Customer> Customers { get; set; } = null!;

    protected override void OnModelCreating(ModelBuilder modelBuilder)
    {
        modelBuilder.Entity<Order>().HasKey(o => o.Id);
        modelBuilder.Entity<Customer>().HasKey(c => c.Id);
    }
}
