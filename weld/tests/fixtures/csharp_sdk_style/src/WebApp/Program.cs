// Minimal ASP.NET Core 6+ top-level-statement Program.cs.
// Exercises the startup-source detection path and confirms the
// SDK-style implicit-glob picks files up without explicit
// <Compile Include> entries.
using Microsoft.AspNetCore.Builder;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.EntityFrameworkCore;
using SdkFixture.WebApp.Data;

namespace SdkFixture.WebApp;

public static class Program
{
    public static void Main(string[] args)
    {
        var builder = WebApplication.CreateBuilder(args);
        builder.Services.AddDbContext<AppDbContext>(opt => opt.UseInMemoryDatabase("app"));
        builder.Services.AddControllers();
        var app = builder.Build();
        app.MapControllers();
        app.Run();
    }
}
