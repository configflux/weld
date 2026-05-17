// Block-scoped namespace style.
using Microsoft.AspNetCore.Builder;

namespace Sample.Web
{
    public static class Program
    {
        public static void Main(string[] args)
        {
            var builder = WebApplication.CreateBuilder(args);
            builder.Build().Run();
        }
    }
}
