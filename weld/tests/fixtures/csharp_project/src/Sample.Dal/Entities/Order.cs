using System.ComponentModel.DataAnnotations.Schema;

namespace Sample.Dal.Entities;

[Table("orders")]
public class Order
{
    public int Id { get; set; }
    public int CustomerId { get; set; }
}
