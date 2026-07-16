using Avalonia;

namespace Reasonix.CapabilityApp;

internal static class Program
{
    [STAThread]
    public static void Main(string[] args)
    {
        if (args.Contains("--automation-smoke"))
        {
            Console.WriteLine("{\"ok\":true,\"app\":\"Reasonix.CapabilityApp\",\"schema_version\":1}");
            return;
        }
        BuildAvaloniaApp().StartWithClassicDesktopLifetime(args);
    }

    public static AppBuilder BuildAvaloniaApp() =>
        AppBuilder.Configure<App>().UsePlatformDetect().LogToTrace();
}
